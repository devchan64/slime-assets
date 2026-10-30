"""맵 원본 전달 묶음의 최초 적재와 세대 기록."""
import hashlib
import json
import math
from psycopg.types.json import Jsonb
from asset_service.content_map_documents import insert_map_document_records, split_map_document_records

def initialize_map_seed_bundle(current_database_connection,current_seed_bundle):
    if not isinstance(current_seed_bundle,dict) or set(current_seed_bundle)!={'documents','buildingProperties'}:
        raise ValueError('documents·buildingProperties가 필요합니다.')
    current_map_documents=current_seed_bundle['documents']
    current_building_properties=current_seed_bundle['buildingProperties']
    if not isinstance(current_map_documents,dict) or not current_map_documents or not isinstance(current_building_properties,dict):
        raise ValueError('맵 시드 형식 오류')
    for current_document_path,current_document_record in current_map_documents.items():
        split_map_document_records(current_document_path,current_document_record)
    for current_asset_identifier,current_property_values in current_building_properties.items():
        if not isinstance(current_property_values,dict) or set(current_property_values)!={'anchorX','anchorY'} or any(type(current_numeric_value) not in (int,float) or not math.isfinite(current_numeric_value) for current_numeric_value in current_property_values.values()):
            raise ValueError('건물 공용 앵커 형식 오류: '+current_asset_identifier)
    current_bundle_hash=hashlib.sha256(json.dumps(current_seed_bundle,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    current_generation_record=current_database_connection.execute('SELECT * FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    if current_generation_record is None:raise ValueError('콘텐츠 스키마 초기화가 필요합니다.')
    current_seed_receipt=current_database_connection.execute("SELECT * FROM content.change_receipts WHERE request_id='map-initial-seed'").fetchone()
    if current_seed_receipt is not None:
        if current_seed_receipt['request_sha256']!=current_bundle_hash:raise ValueError('맵 최초 시드가 이미 적재되었습니다.')
        return current_seed_receipt['response_values']
    if current_database_connection.execute('SELECT 1 FROM content.map_documents LIMIT 1').fetchone():
        raise ValueError('기존 맵 DB에 최초 시드를 적용할 수 없습니다.')
    for current_asset_identifier,current_property_values in current_building_properties.items():
        current_database_connection.execute('INSERT INTO content.building_properties VALUES(%s,%s,%s)',
            (current_asset_identifier,current_property_values['anchorX'],current_property_values['anchorY']))
    for current_document_path,current_document_record in current_map_documents.items():
        insert_map_document_records(current_database_connection,current_document_path,current_document_record)
    current_next_generation=current_generation_record['current_generation']+1
    current_response_values={'generation':current_next_generation,'count':len(current_map_documents),'sha256':current_bundle_hash}
    current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true',(current_next_generation,))
    current_database_connection.execute('''INSERT INTO content.change_receipts
        (request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values)
        VALUES(%s,%s,%s,%s,%s,%s,%s)''',('map-initial-seed',current_bundle_hash,current_next_generation,
        'initial-seed',Jsonb({}),Jsonb(current_response_values),Jsonb(current_response_values)))
    return current_response_values
