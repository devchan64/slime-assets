"""빌보드 폐기와 맵 배치 제거를 같은 DB 트랜잭션으로 처리한다."""
from pathlib import PurePosixPath
from psycopg.types.json import Jsonb
from asset_service.content_asset_queries import read_billboard_asset_record,read_content_generation_state
from asset_service.content_asset_store import ContentRevisionConflict
from asset_service.content_snapshots import hash_content_snapshot_payload


def preview_content_asset_retirement(current_database_connection,current_asset_identifier,current_expected_revision):
    current_asset_record=read_billboard_asset_record(current_database_connection,current_asset_identifier)
    if current_asset_record['retired']:raise ValueError('이미 폐기된 빌보드입니다.')
    if type(current_expected_revision) is not int or current_asset_record['revision']!=current_expected_revision:
        raise ContentRevisionConflict('선택 이후 에셋이 변경되었습니다. 다시 선택하세요.')
    if current_asset_record['metadata'].get('metadataReferences'):raise ValueError('프레임 메타데이터가 있는 에셋은 별도 폐기 검수가 필요합니다.')
    return {'assetId':current_asset_identifier,'expectedRevision':current_expected_revision,
        'expectedGeneration':read_content_generation_state(current_database_connection)['generation'],
        'maps':[{'map':PurePosixPath(current_reference['path']).stem,'path':current_reference['path'],'removed':current_reference['texture_key_occurrences']} for current_reference in current_asset_record['metadata']['mapReferences']],
        'original_preserved':True}


def retire_content_asset_record(current_database_connection,current_request_payload):
    if not isinstance(current_request_payload,dict) or set(current_request_payload)!={'requestId','assetId','expectedRevision','expectedGeneration'}:
        raise ValueError('폐기 요청 필드 오류')
    if not isinstance(current_request_payload['requestId'],str) or not 1<=len(current_request_payload['requestId'])<=200:
        raise ValueError('폐기 requestId 오류')
    current_request_hash=hash_content_snapshot_payload(current_request_payload)
    with current_database_connection.transaction():
        current_generation_record=current_database_connection.execute('SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
        current_receipt_record=current_database_connection.execute('SELECT request_sha256,response_values FROM content.change_receipts WHERE request_id=%s',(current_request_payload['requestId'],)).fetchone()
        if current_receipt_record is not None:
            if current_receipt_record['request_sha256']!=current_request_hash:raise ContentRevisionConflict('폐기 요청 ID 내용 불일치')
            return current_receipt_record['response_values']
        if current_generation_record is None or type(current_request_payload['expectedGeneration']) is not int or current_generation_record['current_generation']!=current_request_payload['expectedGeneration']:
            raise ContentRevisionConflict('폐기 확인 후 맵·에셋이 변경되었습니다. 다시 확인하세요.')
        current_plan_record=preview_content_asset_retirement(current_database_connection,current_request_payload['assetId'],current_request_payload['expectedRevision'])
        current_removed_records=current_database_connection.execute('DELETE FROM content.map_object_records WHERE asset_id=%s RETURNING document_path',(current_request_payload['assetId'],)).fetchall()
        for current_document_path in sorted({current_record['document_path'] for current_record in current_removed_records}):
            current_database_connection.execute('UPDATE content.map_documents SET document_revision=document_revision+1 WHERE document_path=%s',(current_document_path,))
        current_database_connection.execute('UPDATE content.billboard_assets SET retired=true,asset_revision=asset_revision+1 WHERE asset_id=%s',(current_request_payload['assetId'],))
        current_next_generation=current_generation_record['current_generation']+1
        current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true',(current_next_generation,))
        current_response_record={**current_plan_record,'texture':current_request_payload['assetId'],'retired':True,'generation':current_next_generation,'snapshot':'pending'}
        current_database_connection.execute('INSERT INTO content.change_receipts (request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values) VALUES(%s,%s,%s,%s,%s,%s,%s)',(current_request_payload['requestId'],current_request_hash,current_next_generation,'management-retirement',Jsonb(current_plan_record),Jsonb(current_response_record),Jsonb(current_response_record)))
        return current_response_record
