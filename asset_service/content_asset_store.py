"""DB 커밋과 세대 갱신을 공유하는 빌보드 저장소."""
import hashlib
import json
from psycopg import sql
from psycopg.types.json import Jsonb
from asset_service.content_asset_contracts import (
    BILLBOARD_PROPERTY_COLUMN_NAMES, BillboardSaveRequest, decode_billboard_property_record,
)


class ContentRevisionConflict(ValueError):
    """다시 조회한 뒤 적용해야 하는 동시 편집 충돌."""


def save_billboard_property_values(current_database_connection, current_asset_identifier,
        current_request_values, current_editor_identifier, current_image_values=None):
    """호출자의 트랜잭션 안에서 저장하며 파일 스냅샷은 기다리지 않는다."""
    current_save_request = BillboardSaveRequest.model_validate(current_request_values)
    from asset_service.content_asset_seed import ContentImageSeed
    current_image_record=ContentImageSeed.model_validate(current_image_values) if current_image_values is not None else None
    current_request_hash = hashlib.sha256(json.dumps(
        {'asset': current_asset_identifier, 'editor': current_editor_identifier,
         'request': current_save_request.model_dump(), 'image':current_image_record.model_dump() if current_image_record else None}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    # 모든 변경의 세대/멱등 판정을 같은 잠금으로 직렬화한다.
    current_generation_record = current_database_connection.execute(
        'SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    if current_generation_record is None:
        raise ValueError('콘텐츠 DB 초기화가 필요합니다.')
    current_saved_receipt = current_database_connection.execute(
        'SELECT request_sha256,response_values FROM content.change_receipts WHERE request_id=%s',
        (current_save_request.requestId,)).fetchone()
    if current_saved_receipt is not None:
        if current_saved_receipt['request_sha256'] != current_request_hash:
            raise ContentRevisionConflict('동일 요청 ID에 다른 입력을 사용할 수 없습니다.')
        return current_saved_receipt['response_values']
    current_asset_record = current_database_connection.execute(
        'SELECT asset_revision,retired,image_id,image_version,metadata FROM content.billboard_assets WHERE asset_id=%s FOR UPDATE',
        (current_asset_identifier,)).fetchone()
    if current_asset_record is None or current_asset_record['retired']:
        raise ValueError('활성 빌보드가 아닙니다: ' + current_asset_identifier)
    if current_asset_record['asset_revision'] != current_save_request.expectedRevision:
        raise ContentRevisionConflict('에셋이 변경되었습니다. 최신 값을 조회하세요.')
    if current_image_record is not None:
        if current_asset_record['metadata'].get('metadataReferences'):
            raise ValueError('프레임 메타데이터가 있는 이미지는 별도 검수가 필요합니다.')
        current_database_connection.execute('INSERT INTO content.image_versions (image_id,image_version,source_path,image_sha256,image_width,image_height,published_path) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
            (current_image_record.imageId,current_image_record.version,current_image_record.path,current_image_record.sha256,current_image_record.width,current_image_record.height,current_image_record.publishedPath))
        current_existing_image=current_database_connection.execute('SELECT * FROM content.image_versions WHERE image_id=%s AND image_version=%s',(current_image_record.imageId,current_image_record.version)).fetchone()
        if (current_existing_image['source_path'],current_existing_image['image_sha256'],current_existing_image['image_width'],current_existing_image['image_height'],current_existing_image['published_path'])!=(current_image_record.path,current_image_record.sha256,current_image_record.width,current_image_record.height,current_image_record.publishedPath):
            raise ContentRevisionConflict('기존 이미지 버전의 내용은 변경할 수 없습니다.')
        current_database_connection.execute('UPDATE content.billboard_assets SET image_id=%s,image_version=%s WHERE asset_id=%s',(current_image_record.imageId,current_image_record.version,current_asset_identifier))
    current_property_record = current_database_connection.execute(
        'SELECT * FROM content.billboard_properties WHERE asset_id=%s',
        (current_asset_identifier,)).fetchone()
    if current_property_record is None:
        raise ValueError('공용 속성이 등록되지 않았습니다: ' + current_asset_identifier)
    current_before_values = decode_billboard_property_record(current_property_record)
    current_after_values = current_save_request.properties.model_dump(exclude_none=True)
    current_property_values = current_save_request.properties.model_dump()
    current_update_statement = sql.SQL('UPDATE content.billboard_properties SET {} WHERE asset_id=%s').format(
        sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(current_column_name))
            for current_column_name in BILLBOARD_PROPERTY_COLUMN_NAMES.values()))
    current_database_connection.execute(current_update_statement,
        tuple(current_property_values[current_field_name] for current_field_name in BILLBOARD_PROPERTY_COLUMN_NAMES)
        + (current_asset_identifier,))
    current_next_revision = current_asset_record['asset_revision'] + 1
    current_next_generation = current_generation_record['current_generation'] + 1
    current_database_connection.execute(
        'UPDATE content.billboard_assets SET asset_revision=%s WHERE asset_id=%s',
        (current_next_revision, current_asset_identifier))
    current_database_connection.execute(
        'UPDATE content.generations SET current_generation=%s WHERE singleton=true', (current_next_generation,))
    current_response_values = {'saved': True, 'asset': current_asset_identifier,
        'revision': current_next_revision, 'generation': current_next_generation,
        'properties': current_after_values, 'snapshot': 'pending'}
    if current_image_record is not None:current_response_values['image']=current_image_record.model_dump()
    current_database_connection.execute('''INSERT INTO content.change_receipts
        (request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values)
        VALUES(%s,%s,%s,%s,%s,%s,%s)''',
        (current_save_request.requestId, current_request_hash, current_next_generation,
         current_editor_identifier, Jsonb({'asset': current_asset_identifier, 'properties': current_before_values,'imageId':current_asset_record['image_id'],'imageVersion':current_asset_record['image_version']}),
         Jsonb({'asset': current_asset_identifier, 'properties': current_after_values,'image':current_image_record.model_dump() if current_image_record else None}), Jsonb(current_response_values)))
    return current_response_values
