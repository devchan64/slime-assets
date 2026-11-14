"""로컬 API 시작 전 원장 변경을 원자적으로 반영한다."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
from uuid import uuid4

from psycopg import sql
from psycopg.types.json import Jsonb
from asset_service.connection import open_asset_service_connection
from asset_service.content_asset_contracts import BILLBOARD_PROPERTY_COLUMN_NAMES
from asset_service.content_asset_store import ContentRevisionConflict
from asset_service.content_effect_assets import seed_effect_asset_records, hash_effect_asset_document
from asset_service.content_map_documents import insert_map_document_records, read_map_document_records
from asset_service.ledger_source import AssetLedgerSource
from asset_service.content_building_properties import validate_building_property_values


JSON_RECORD_COLUMN_NAMES = frozenset(('metadata', 'document', 'display_adjustments'))


def is_initial_migration_state(current_database_connection, current_generation_record):
    if current_generation_record['current_generation'] != 1 or current_generation_record['snapshot_generation'] != 0:
        return False
    current_tower_records = current_database_connection.execute('SELECT asset_id, anchor_x, anchor_y, display_adjustments FROM content.building_properties').fetchall()
    if current_tower_records != [{'asset_id': 'ward-tower-v1', 'anchor_x': Decimal('0.5'), 'anchor_y': Decimal('0.8203125'), 'display_adjustments': {}}]:
        return False
    for current_table_name in ('asset_types', 'image_versions', 'billboard_assets', 'billboard_properties', 'map_documents', 'map_grid_cells', 'map_object_records', 'effect_assets', 'change_receipts', 'snapshot_runs'):
        if current_database_connection.execute(sql.SQL('SELECT 1 FROM content.{} LIMIT 1').format(sql.Identifier(current_table_name))).fetchone():
            return False
    return True


def upsert_ledger_record(current_database_connection, current_table_name, current_record_values, current_primary_columns, current_revision_column=None):
    current_filter_clause = sql.SQL(' AND ').join(sql.SQL('{}=%s').format(sql.Identifier(current_column_name)) for current_column_name in current_primary_columns)
    current_existing_record = current_database_connection.execute(
        sql.SQL('SELECT * FROM content.{} WHERE {}').format(sql.Identifier(current_table_name), current_filter_clause),
        tuple(current_record_values[current_column_name] for current_column_name in current_primary_columns)).fetchone()
    if current_existing_record and all((float(current_existing_record[current_column_name]) if isinstance(current_existing_record[current_column_name], Decimal) else current_existing_record[current_column_name]) == current_column_value for current_column_name, current_column_value in current_record_values.items()):
        return False
    if current_revision_column:
        current_record_values = {**current_record_values, current_revision_column: (current_existing_record[current_revision_column] + 1 if current_existing_record else 1)}
    current_column_names = list(current_record_values)
    current_update_columns = [current_column_name for current_column_name in current_column_names if current_column_name not in current_primary_columns]
    current_database_connection.execute(sql.SQL('INSERT INTO content.{} ({}) VALUES ({}) ON CONFLICT ({}) DO UPDATE SET {}').format(
        sql.Identifier(current_table_name), sql.SQL(',').join(map(sql.Identifier, current_column_names)),
        sql.SQL(',').join(sql.Placeholder() for current_column_name in current_column_names),
        sql.SQL(',').join(map(sql.Identifier, current_primary_columns)),
        sql.SQL(',').join(sql.SQL('{}=EXCLUDED.{}').format(sql.Identifier(current_column_name), sql.Identifier(current_column_name)) for current_column_name in current_update_columns)),
        tuple(Jsonb(current_record_values[current_column_name]) if current_column_name in JSON_RECORD_COLUMN_NAMES else current_record_values[current_column_name] for current_column_name in current_column_names))
    return True


def synchronize_ledger_bundle(current_database_connection, current_seed_bundle, current_verify_callback):
    """호출자가 트랜잭션을 소유한다. 실패 시 전체 롤백해야 한다."""
    current_generation_record = current_database_connection.execute('SELECT * FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    if current_generation_record is None:
        raise ValueError('콘텐츠 스키마 초기화가 필요합니다.')
    if (current_generation_record['current_generation'] != current_generation_record['snapshot_generation']
            and not is_initial_migration_state(current_database_connection, current_generation_record)):
        raise ContentRevisionConflict('원장 미반영 DB 편집이 있어 시작을 중단합니다. 기존 원장 출력 기능으로 DB 편집을 보존·검수한 뒤 다시 실행하세요. '
                                      f"DB 세대={current_generation_record['current_generation']}, 원장 반영 세대={current_generation_record['snapshot_generation']}")
    if current_database_connection.execute("SELECT 1 FROM content.snapshot_runs WHERE state='running'").fetchone():
        raise ContentRevisionConflict('원장 출력 작업이 남아 있습니다. 완료 또는 실패 복구 후 다시 실행하세요.')
    if not current_database_connection.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='content' AND table_name='building_properties' AND column_name='display_adjustments'").fetchone():
        raise ValueError('003_building_display_adjustments 마이그레이션이 필요합니다. scripts/setup_asset_local.sh 실행 후 다시 시작하세요.')
    current_change_records = []
    current_billboard_bundle = current_seed_bundle['billboards']
    for current_type_identifier, current_type_label in current_billboard_bundle['types'].items():
        if upsert_ledger_record(current_database_connection, 'asset_types', {'asset_type': current_type_identifier, 'display_name': current_type_label}, ('asset_type',)):
            current_change_records.append('type:' + current_type_identifier)
    for current_asset_record in current_billboard_bundle['assets']:
        current_image_record = current_asset_record['image']
        current_image_values = {'image_id': current_image_record['imageId'], 'image_version': current_image_record['version'],
            'source_path': current_image_record['path'], 'image_sha256': current_image_record['sha256'],
            'image_width': current_image_record['width'], 'image_height': current_image_record['height'], 'published_path': current_image_record['publishedPath']}
        current_existing_image = current_database_connection.execute('SELECT * FROM content.image_versions WHERE image_id=%s AND image_version=%s',
            (current_image_values['image_id'], current_image_values['image_version'])).fetchone()
        if current_existing_image:
            current_image_values['published_path'] = current_existing_image['published_path']
        if current_existing_image and current_existing_image != current_image_values:
            raise ContentRevisionConflict('불변 이미지 버전이 변경되었습니다. 새 버전으로 등록하세요: ' + current_image_record['imageId'])
        if not current_existing_image:
            current_database_connection.execute('INSERT INTO content.image_versions VALUES(%s,%s,%s,%s,%s,%s,%s)', tuple(current_image_values.values()))
        current_asset_identifier = current_asset_record['assetId']
        current_asset_changed = upsert_ledger_record(current_database_connection, 'billboard_assets', {
            'asset_id': current_asset_identifier, 'asset_type': current_asset_record['assetType'], 'display_name': current_asset_record['name'],
            'image_id': current_image_record['imageId'], 'image_version': current_image_record['version'], 'retired': False,
            'metadata': current_asset_record['metadata']}, ('asset_id',), 'asset_revision')
        current_properties_changed = upsert_ledger_record(current_database_connection, 'billboard_properties', {
            'asset_id': current_asset_identifier, **{current_column_name: current_asset_record['properties'].get(current_field_name)
            for current_field_name, current_column_name in BILLBOARD_PROPERTY_COLUMN_NAMES.items()}}, ('asset_id',))
        if current_properties_changed and not current_asset_changed:
            current_database_connection.execute('UPDATE content.billboard_assets SET asset_revision=asset_revision+1 WHERE asset_id=%s', (current_asset_identifier,))
        if current_asset_changed or current_properties_changed:
            current_change_records.append('billboard:' + current_asset_identifier)
    current_desired_documents = current_seed_bundle['maps']['documents']
    current_existing_documents = {current_document_record['document_path']: current_document_record['document_revision']
        for current_document_record in current_database_connection.execute('SELECT document_path,document_revision FROM content.map_documents').fetchall()}
    for current_document_path in sorted(set(current_existing_documents) | set(current_desired_documents)):
        current_document_exists = current_document_path in current_existing_documents
        if current_document_exists and current_document_path in current_desired_documents and read_map_document_records(current_database_connection, current_document_path) == current_desired_documents[current_document_path]:
            continue
        if current_document_exists:
            for current_table_name in ('map_grid_cells', 'map_object_records', 'map_documents'):
                current_database_connection.execute(sql.SQL('DELETE FROM content.{} WHERE document_path=%s').format(sql.Identifier(current_table_name)), (current_document_path,))
        if current_document_path in current_desired_documents:
            insert_map_document_records(current_database_connection, current_document_path, current_desired_documents[current_document_path])
            current_database_connection.execute('UPDATE content.map_documents SET document_revision=%s WHERE document_path=%s',
                (max(current_existing_documents.get(current_document_path, 0) + 1, current_generation_record['current_generation'] + 1), current_document_path))
        current_change_records.append('map:' + current_document_path)
    current_desired_identifiers = {current_asset_record['assetId'] for current_asset_record in current_billboard_bundle['assets']}
    for current_existing_asset in current_database_connection.execute('SELECT asset_id FROM content.billboard_assets WHERE NOT retired').fetchall():
        current_asset_identifier = current_existing_asset['asset_id']
        if current_asset_identifier not in current_desired_identifiers:
            if current_database_connection.execute('SELECT 1 FROM content.map_object_records WHERE asset_id=%s LIMIT 1', (current_asset_identifier,)).fetchone():
                raise ContentRevisionConflict('원장에서 제거한 빌보드가 맵에서 사용 중입니다: ' + current_asset_identifier)
            current_database_connection.execute('UPDATE content.billboard_assets SET retired=true,asset_revision=asset_revision+1 WHERE asset_id=%s', (current_asset_identifier,))
            current_change_records.append('retire:' + current_asset_identifier)
    current_building_records = current_seed_bundle['maps']['buildingProperties']
    for current_asset_identifier, current_property_values in current_building_records.items():
        current_display_adjustments = validate_building_property_values(current_property_values)
        if upsert_ledger_record(current_database_connection, 'building_properties', {'asset_id': current_asset_identifier,
            'anchor_x': current_property_values['anchorX'], 'anchor_y': current_property_values['anchorY'], 'display_adjustments': current_display_adjustments}, ('asset_id',)):
            current_change_records.append('building:' + current_asset_identifier)
    current_effect_records = current_seed_bundle['effects']
    current_effect_identifiers = [current_effect_record['id'] for current_effect_record in current_effect_records]
    if len(set(current_effect_identifiers)) != len(current_effect_identifiers):
        raise ValueError('이펙트 ID 중복')
    for current_effect_record in current_effect_records:
        current_asset_identifier = current_effect_record['id']
        current_existing_effect = current_database_connection.execute('SELECT document FROM content.effect_assets WHERE asset_id=%s', (current_asset_identifier,)).fetchone()
        if current_existing_effect and current_existing_effect['document'] != current_effect_record:
            current_database_connection.execute('DELETE FROM content.effect_assets WHERE asset_id=%s', (current_asset_identifier,))
        seed_effect_asset_records(current_database_connection, [current_effect_record])
        if not current_existing_effect or current_existing_effect['document'] != current_effect_record:
            current_change_records.append('effect:' + current_asset_identifier)
    for current_table_name, current_expected_identifiers in (('building_properties', set(current_building_records)), ('effect_assets', set(current_effect_identifiers))):
        for current_existing_record in current_database_connection.execute(sql.SQL('SELECT asset_id FROM content.{}').format(sql.Identifier(current_table_name))).fetchall():
            current_asset_identifier = current_existing_record['asset_id']
            if current_asset_identifier not in current_expected_identifiers:
                current_database_connection.execute(sql.SQL('DELETE FROM content.{} WHERE asset_id=%s').format(sql.Identifier(current_table_name)), (current_asset_identifier,))
                current_change_records.append('remove:' + current_table_name + ':' + current_asset_identifier)
    current_verify_callback()
    current_next_generation = current_generation_record['current_generation'] + bool(current_change_records)
    current_response_values = {'generation': current_next_generation, 'changes': current_change_records, 'sha256': hash_effect_asset_document(current_seed_bundle)}
    if current_change_records:
        current_database_connection.execute('UPDATE content.generations SET current_generation=%s,snapshot_generation=%s WHERE singleton=true', (current_next_generation, current_next_generation))
        current_database_connection.execute('''INSERT INTO content.change_receipts
            (request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values)
            VALUES(%s,%s,%s,%s,%s,%s,%s)''', ('ledger-startup-' + str(uuid4()), current_response_values['sha256'],
            current_next_generation, 'local-ledger-startup', Jsonb({'generation': current_generation_record['current_generation']}),
            Jsonb(current_response_values), Jsonb(current_response_values)))
    return current_response_values


def run_local_ledger_sync():
    import os
    current_argument_parser = argparse.ArgumentParser(description='Git 작업 트리 원장 → 로컬 DB 동기화')
    current_argument_parser.add_argument('--check', action='store_true', help='전체 반영을 검증한 뒤 롤백한다.')
    current_argument_values = current_argument_parser.parse_args()
    if os.environ.get('ASSET_DB_STAGE', 'local') != 'local':
        raise ValueError('원장 시작 동기화는 local 단계 전용입니다.')
    current_ledger_source = AssetLedgerSource(Path(__file__).resolve().parents[1])
    current_seed_bundle = current_ledger_source.build_ledger_bundle()
    with open_asset_service_connection() as current_database_connection:
        current_response_values = synchronize_ledger_bundle(current_database_connection, current_seed_bundle, current_ledger_source.verify_ledger_unchanged)
        if current_argument_values.check:
            current_database_connection.rollback()
    print(json.dumps({'checkOnly': current_argument_values.check, **current_response_values}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    run_local_ledger_sync()
