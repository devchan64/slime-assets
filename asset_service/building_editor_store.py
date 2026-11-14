"""건물 공용 앵커의 동시 편집 보호와 원장 세대 기록."""
import hashlib
import json
import math
import re
from psycopg.types.json import Jsonb
from asset_service.content_building_properties import validate_building_property_values, BUILDING_SHADOW_PROPERTY_FIELDS
from asset_service.content_asset_store import ContentRevisionConflict


def save_building_anchor_values(current_database_connection, current_asset_identifier, current_request_payload):
    if not re.fullmatch(r'(?:town-[a-z0-9-]+-v[1-9][0-9]*|ward-tower-v1)',current_asset_identifier):
        raise ValueError('건물 ID 오류')
    if not isinstance(current_request_payload,dict) or set(current_request_payload)!={'requestId','expectedGeneration','expected','properties'}:
        raise ValueError('requestId·expectedGeneration·expected·properties가 필요합니다.')
    current_request_identifier=current_request_payload['requestId']
    if not isinstance(current_request_identifier,str) or not 1<=len(current_request_identifier)<=128 or type(current_request_payload['expectedGeneration']) is not int:
        raise ValueError('요청 ID 또는 세대 오류')
    for current_property_name in ('expected','properties'):
        current_property_values=current_request_payload[current_property_name]
        validate_building_property_values(current_property_values)
        if not isinstance(current_property_values,dict) or set(current_property_values)-BUILDING_SHADOW_PROPERTY_FIELDS not in ({'anchorX','anchorY'},{'anchorX','anchorY','displayWidthAdjustment','displayHeightAdjustment'}) or any(type(current_property_values[k]) not in (int,float) or not math.isfinite(current_property_values[k]) or not 0<=current_property_values[k]<=16 for k in ('anchorX','anchorY')) or any(type(current_property_values[k]) is not int or not -2048<=current_property_values[k]<=2048 for k in ('displayWidthAdjustment','displayHeightAdjustment') if k in current_property_values):
            raise ValueError('건물 앵커는 0~16 범위의 X·Y여야 합니다.')
    if current_asset_identifier == 'ward-tower-v1' and any(current_request_payload['properties'].get(current_field_name,0) < -138 for current_field_name in ('displayWidthAdjustment','displayHeightAdjustment')):
        raise ValueError('결계탑 크기 보정은 -138px 이상이어야 합니다.')
    current_request_hash=hashlib.sha256(json.dumps({'asset':current_asset_identifier,'request':current_request_payload},sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    current_generation_record=current_database_connection.execute('SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    if current_generation_record is None:raise ValueError('콘텐츠 DB 초기화가 필요합니다.')
    current_receipt_record=current_database_connection.execute('SELECT request_sha256,response_values FROM content.change_receipts WHERE request_id=%s',(current_request_identifier,)).fetchone()
    if current_receipt_record:
        if current_receipt_record['request_sha256']!=current_request_hash:raise ContentRevisionConflict('동일 요청 ID의 입력 불일치')
        return current_receipt_record['response_values']
    if current_generation_record['current_generation']!=current_request_payload['expectedGeneration']:raise ContentRevisionConflict('콘텐츠가 변경되었습니다. 새로고침하세요.')
    current_property_record=current_database_connection.execute("SELECT anchor_x,anchor_y,display_adjustments,COALESCE((display_adjustments->>'displayWidthAdjustment')::integer,0) AS display_width_adjustment,COALESCE((display_adjustments->>'displayHeightAdjustment')::integer,0) AS display_height_adjustment FROM content.building_properties WHERE asset_id=%s FOR UPDATE",(current_asset_identifier,)).fetchone()
    if current_property_record is None:raise ValueError('등록되지 않은 건물입니다.')
    previous_property_values={'anchorX':float(current_property_record['anchor_x']),'anchorY':float(current_property_record['anchor_y'])}
    if 'displayWidthAdjustment' in current_request_payload['expected']:
        previous_property_values.update({'displayWidthAdjustment':current_property_record['display_width_adjustment'],'displayHeightAdjustment':current_property_record['display_height_adjustment']})
    previous_property_values.update({current_field_name:current_property_record.get('display_adjustments',{}).get(current_field_name) for current_field_name in BUILDING_SHADOW_PROPERTY_FIELDS if current_field_name in current_request_payload['expected']})
    if previous_property_values!=current_request_payload['expected']:raise ContentRevisionConflict('건물 앵커가 변경되었습니다. 새로고침하세요.')
    updated_property_values={**current_property_record.get('display_adjustments',{}),'displayWidthAdjustment':current_property_record['display_width_adjustment'],'displayHeightAdjustment':current_property_record['display_height_adjustment'],**current_request_payload['properties']};next_generation_number=current_generation_record['current_generation']+1
    current_database_connection.execute('UPDATE content.building_properties SET anchor_x=%s,anchor_y=%s,display_adjustments=%s WHERE asset_id=%s',(updated_property_values['anchorX'],updated_property_values['anchorY'],Jsonb(validate_building_property_values(updated_property_values)),current_asset_identifier))
    current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true',(next_generation_number,))
    current_response_record={'saved':True,'asset':current_asset_identifier,'generation':next_generation_number,'properties':updated_property_values,'snapshot':'pending'}
    current_database_connection.execute('INSERT INTO content.change_receipts (request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values) VALUES(%s,%s,%s,%s,%s,%s,%s)',(current_request_identifier,current_request_hash,next_generation_number,'management-gateway',Jsonb({'asset':current_asset_identifier,'properties':previous_property_values}),Jsonb({'asset':current_asset_identifier,'properties':updated_property_values}),Jsonb(current_response_record)))
    return current_response_record
