"""맵 문서 묶음의 revision·세대 조건부 원자 저장."""
import hashlib
import json
from psycopg.types.json import Jsonb
from asset_service.content_asset_store import ContentRevisionConflict
from asset_service.content_map_documents import insert_map_document_records, read_map_document_records, split_map_document_records, validate_public_map_path


def save_content_map_documents(current_database_connection,current_request_payload):
    if not isinstance(current_request_payload,dict) or set(current_request_payload)!={'requestId','expectedGeneration','documents'}:
        raise ValueError('requestId·expectedGeneration·documents가 필요합니다.')
    current_request_identifier=current_request_payload['requestId']
    if not isinstance(current_request_identifier,str) or not 1<=len(current_request_identifier)<=180:raise ValueError('요청 ID 오류')
    if type(current_request_payload['expectedGeneration']) is not int:raise ValueError('세대는 정수여야 합니다.')
    current_request_hash=hashlib.sha256(json.dumps(current_request_payload,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    current_generation_record=current_database_connection.execute('SELECT * FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    current_saved_receipt=current_database_connection.execute('SELECT * FROM content.change_receipts WHERE request_id=%s',(current_request_identifier,)).fetchone()
    if current_saved_receipt:
        if current_saved_receipt['request_sha256']!=current_request_hash:raise ContentRevisionConflict('동일 요청 ID의 입력 불일치')
        return current_saved_receipt['response_values']
    if current_generation_record is None or current_generation_record['current_generation']!=current_request_payload['expectedGeneration']:
        raise ContentRevisionConflict('맵 또는 공용 에셋 속성이 변경되었습니다. 다시 검증하세요.')
    current_document_changes=current_request_payload['documents']
    if not isinstance(current_document_changes,list) or not 1<=len(current_document_changes)<=10:raise ValueError('맵 문서 변경 수 오류')
    current_before_records={}
    current_after_records={}
    current_revision_records={}
    for current_change_record in current_document_changes:
        if not isinstance(current_change_record,dict) or set(current_change_record)!={'path','revision','document'}:raise ValueError('맵 변경 필드 오류')
        current_document_path=current_change_record['path']
        validate_public_map_path(current_document_path)
        if current_document_path in current_before_records:raise ValueError('맵 문서 중복')
        current_old_record=current_database_connection.execute('SELECT * FROM content.map_documents WHERE document_path=%s FOR UPDATE',(current_document_path,)).fetchone()
        if current_old_record is None or type(current_change_record['revision']) is not int or current_old_record['document_revision']!=current_change_record['revision']:
            raise ContentRevisionConflict('맵 문서 revision 불일치: '+current_document_path)
        current_document_record=current_change_record['document']
        split_map_document_records(current_document_path,current_document_record)
        current_before_record=read_map_document_records(current_database_connection,current_document_path)
        current_optional_fields={'cliffOverrides'}
        if current_document_path.startswith('field_tiles/') and current_document_path not in ('field_tiles/index.yaml','field_tiles/codes.yaml'):
            current_optional_fields.add('groundOverrides')
        if current_document_path.startswith('terrain/maps/'):
            current_optional_fields.update({'bridges', 'terrainEffects', 'ambientTint'})
            if 'bridges' in current_document_record['data']:
                from asset_service.terrain_bridges import TerrainBridgeDefinition, validate_bridge_geometry
                current_bridge_records=[TerrainBridgeDefinition.model_validate(current_bridge_value) for current_bridge_value in current_document_record['data']['bridges']]
                validate_bridge_geometry(current_bridge_records,current_document_record['data']['elevations'],current_document_record['data']['columns'],current_document_record['data']['rows'])
        if current_document_record['managementId']!=current_old_record['management_id'] or set(current_document_record['data'])-set(current_before_record['data'])-current_optional_fields or set(current_before_record['data'])-set(current_document_record['data'])-current_optional_fields:
            raise ValueError('맵 ID 또는 허용 필드 불일치')
        current_before_records[current_document_path]=current_before_record
        current_after_records[current_document_path]=current_document_record
        current_revision_records[current_document_path]=current_change_record['revision']+1
    from asset_service.terrain_effects import validate_terrain_effect_records
    for current_document_path, current_document_record in current_after_records.items():
        if current_document_path.startswith('terrain/maps/'):
            current_map_data = current_document_record['data']
            from asset_service.terrain_ambient import validate_terrain_ambient_tint
            validate_terrain_ambient_tint(current_map_data)
            current_tiles_path = current_document_path.replace('terrain/maps/', 'field_tiles/')
            current_tiles_document = current_after_records.get(current_tiles_path)
            if current_tiles_document is None:
                current_tiles_document = read_map_document_records(current_database_connection, current_tiles_path)
            validate_terrain_effect_records(current_map_data.get('terrainEffects', []), current_map_data['columns'], current_map_data['rows'], current_tiles_document['data'].get('objects', []))
    for current_document_path, current_document_record in current_after_records.items():
        if current_document_path.startswith('field_tiles/') and current_document_path not in ('field_tiles/index.yaml', 'field_tiles/codes.yaml'):
            current_terrain_path=current_document_path.replace('field_tiles/', 'terrain/maps/')
            current_terrain_document=current_after_records.get(current_terrain_path)
            if current_terrain_document is None:
                current_terrain_document=read_map_document_records(current_database_connection,current_terrain_path)
            current_map_data=current_terrain_document['data']
            validate_terrain_effect_records(current_map_data.get('terrainEffects',[]),current_map_data['columns'],current_map_data['rows'],current_document_record['data'].get('objects',[]))
    for current_document_path,current_document_record in current_after_records.items():
        current_database_connection.execute('DELETE FROM content.map_grid_cells WHERE document_path=%s',(current_document_path,))
        current_database_connection.execute('DELETE FROM content.map_object_records WHERE document_path=%s',(current_document_path,))
        current_database_connection.execute('DELETE FROM content.map_documents WHERE document_path=%s',(current_document_path,))
        insert_map_document_records(current_database_connection,current_document_path,current_document_record)
        current_database_connection.execute('UPDATE content.map_documents SET document_revision=%s WHERE document_path=%s',
            (current_revision_records[current_document_path],current_document_path))
    current_next_generation=current_generation_record['current_generation']+1
    current_response_record={'saved':True,'generation':current_next_generation,'revisions':current_revision_records,'snapshot':'pending'}
    current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true',(current_next_generation,))
    current_database_connection.execute('''INSERT INTO content.change_receipts
        (request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values)
        VALUES(%s,%s,%s,%s,%s,%s,%s)''',(current_request_identifier,current_request_hash,current_next_generation,
        'management-gateway',Jsonb(current_before_records),Jsonb(current_after_records),Jsonb(current_response_record)))
    return current_response_record
