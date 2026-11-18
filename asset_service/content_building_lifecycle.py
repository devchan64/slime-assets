"""건물 공용 속성의 조건부 등록과 사용 중 폐기 차단."""
import hashlib
import json
import math
import re

from psycopg.types.json import Jsonb
from asset_service.content_asset_store import ContentRevisionConflict
from asset_service.content_building_properties import decode_building_property_record
from asset_service.content_map_documents import read_map_document_records


def change_asset_building_lifecycle(current_database_connection, current_asset_identifier, current_request_payload, current_action_name):
    if current_action_name not in ('register', 'retire') or not isinstance(current_asset_identifier,str) or not re.fullmatch(r'town-[a-z0-9-]+-v[1-9][0-9]*',current_asset_identifier):
        raise ValueError('건물 ID 또는 명령 오류. 공용 결계탑은 등록·폐기할 수 없습니다.')
    current_required_fields={'requestId','expectedGeneration'} | ({'properties'} if current_action_name=='register' else set())
    if not isinstance(current_request_payload,dict) or set(current_request_payload)!=current_required_fields:
        raise ValueError('건물 명령 필드 오류')
    current_request_identifier=current_request_payload['requestId']
    if not isinstance(current_request_identifier,str) or not 1<=len(current_request_identifier)<=128 or type(current_request_payload['expectedGeneration']) is not int or current_request_payload['expectedGeneration']<0:
        raise ValueError('요청 ID 또는 세대 오류')
    current_property_values=current_request_payload.get('properties')
    if current_action_name=='register' and (not isinstance(current_property_values,dict) or set(current_property_values)!={'anchorX','anchorY'} or any(type(current_value) not in (int,float) or not math.isfinite(current_value) or not 0<=current_value<=16 for current_value in current_property_values.values())):
        raise ValueError('건물 앵커는 0~16 범위여야 합니다.')
    current_request_hash=hashlib.sha256(json.dumps({'asset':current_asset_identifier,'action':current_action_name,'request':current_request_payload},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    current_generation_record=current_database_connection.execute('SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    current_receipt_record=current_database_connection.execute('SELECT request_sha256,response_values FROM content.change_receipts WHERE request_id=%s',(current_request_identifier,)).fetchone()
    if current_receipt_record:
        if current_receipt_record['request_sha256']!=current_request_hash:raise ContentRevisionConflict('동일 요청 ID의 입력 불일치')
        return current_receipt_record['response_values']
    if current_generation_record is None or current_generation_record['current_generation']!=current_request_payload['expectedGeneration']:
        raise ContentRevisionConflict('콘텐츠가 변경되었습니다. 새로고침하세요.')
    current_building_record=current_database_connection.execute('SELECT * FROM content.building_properties WHERE asset_id=%s FOR UPDATE',(current_asset_identifier,)).fetchone()
    if current_action_name=='register':
        if current_building_record is not None:raise ValueError('이미 등록된 건물입니다.')
        if current_database_connection.execute('SELECT 1 FROM content.billboard_assets WHERE asset_id=%s',(current_asset_identifier,)).fetchone():raise ValueError('이미 빌보드에 사용 중인 ID입니다.')
        current_database_connection.execute('INSERT INTO content.building_properties (asset_id,anchor_x,anchor_y,display_adjustments) VALUES (%s,%s,%s,%s)',(current_asset_identifier,current_property_values['anchorX'],current_property_values['anchorY'],Jsonb({})))
        current_before_values={}
        current_after_values={'asset':current_asset_identifier,'properties':current_property_values}
    else:
        if current_building_record is None:raise ValueError('등록되지 않은 건물입니다.')
        current_city_index=read_map_document_records(current_database_connection,'city_layouts/index.yaml')['data']
        current_city_identifiers=current_city_index['includes']['layouts']
        if not isinstance(current_city_identifiers,(dict,list)) or any(not isinstance(current_map_identifier,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,95}',current_map_identifier) for current_map_identifier in current_city_identifiers):
            raise ValueError('도시 맵 목록 형식 오류')
        current_usage_records=[current_record['document_path'] for current_record in current_database_connection.execute('SELECT DISTINCT document_path FROM content.map_object_records WHERE asset_id=%s',(current_asset_identifier,)).fetchall()]
        for current_map_identifier in current_city_identifiers:
            current_map_values=read_map_document_records(current_database_connection,'city_layouts/'+current_map_identifier+'.yaml')['data']
            current_building_values=current_map_values.get('buildings')
            if not isinstance(current_building_values,list):raise ValueError('도시 건물 목록 형식 오류')
            if any(not isinstance(current_entry_value,dict) or not isinstance(current_entry_value.get('sprite'),dict) for current_entry_value in current_building_values):raise ValueError('도시 건물 배치 형식 오류')
            if any(current_entry_value['sprite'].get('textureKey')==current_asset_identifier for current_entry_value in current_building_values):current_usage_records.append(current_map_identifier)
        if current_usage_records:raise ValueError('사용 중인 건물은 삭제할 수 없습니다: '+', '.join(current_usage_records))
        current_before_values={'asset':current_asset_identifier,'properties':decode_building_property_record(current_building_record)}
        current_after_values={}
        current_database_connection.execute('DELETE FROM content.building_properties WHERE asset_id=%s',(current_asset_identifier,))
    current_next_generation=current_generation_record['current_generation']+1
    current_result_record={'saved':True,'asset':current_asset_identifier,'action':current_action_name,'generation':current_next_generation,'snapshot':'pending'}
    current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true',(current_next_generation,))
    current_database_connection.execute('INSERT INTO content.change_receipts (request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values) VALUES(%s,%s,%s,%s,%s,%s,%s)',(current_request_identifier,current_request_hash,current_next_generation,'management-gateway',Jsonb(current_before_values),Jsonb(current_after_values),Jsonb(current_result_record)))
    return current_result_record
