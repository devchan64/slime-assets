"""전체 최초 적재를 단일 트랜잭션과 묶음 영수증으로 보호한다."""
from psycopg.types.json import Jsonb
from asset_service.content_asset_seed import BillboardSeedBundle, initialize_billboard_seed_bundle
from asset_service.content_map_seed import initialize_map_seed_bundle
from asset_service.content_effect_assets import seed_effect_asset_records, hash_effect_asset_document
from asset_service.content_asset_store import ContentRevisionConflict


def initialize_complete_seed_bundle(current_database_connection, current_seed_bundle):
    if set(current_seed_bundle) not in ({'billboards','maps'}, {'billboards','maps','effects'}):
        raise ValueError('billboards·maps 시드 묶음이 필요합니다.')
    current_billboard_bundle = BillboardSeedBundle.model_validate(current_seed_bundle['billboards'])
    current_bundle_hash = hash_effect_asset_document(current_seed_bundle)
    current_database_connection.execute('SELECT * FROM content.generations WHERE singleton=true FOR UPDATE')
    current_seed_receipt = current_database_connection.execute(
        "SELECT * FROM content.change_receipts WHERE request_id='complete-initial-seed'").fetchone()
    if current_seed_receipt:
        if current_seed_receipt['request_sha256'] != current_bundle_hash:
            raise ContentRevisionConflict('전체 최초 시드가 이미 적재되었습니다. 변경 import 계획이 필요합니다.')
        return current_seed_receipt['response_values']
    if current_database_connection.execute('SELECT 1 FROM content.change_receipts LIMIT 1').fetchone():
        raise ValueError('전체 최초 적재는 비어 있는 DB에서만 가능합니다.')
    if 'effects' in current_seed_bundle:
        seed_effect_asset_records(current_database_connection, current_seed_bundle['effects'])
    current_billboard_result = initialize_billboard_seed_bundle(current_database_connection, current_billboard_bundle.model_dump())
    current_map_result = initialize_map_seed_bundle(current_database_connection, current_seed_bundle['maps'])
    current_next_generation = current_map_result['generation'] + 1
    current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true', (current_next_generation,))
    current_response_values = {'billboards':current_billboard_result, 'maps':current_map_result, 'generation':current_next_generation}
    current_database_connection.execute('''INSERT INTO content.change_receipts
        (request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values)
        VALUES(%s,%s,%s,%s,%s,%s,%s)''', ('complete-initial-seed',current_bundle_hash,
        current_next_generation,'initial-seed',Jsonb({}),Jsonb(current_response_values),Jsonb(current_response_values)))
    return current_response_values
