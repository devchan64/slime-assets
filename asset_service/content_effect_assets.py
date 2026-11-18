"""빌보드 점유·그림자 속성과 분리된 전용 애니메이션 이펙트 원장."""
from psycopg.types.json import Jsonb
from pathlib import PurePosixPath


def seed_effect_asset_records(current_database_connection,current_effect_records):
    if not isinstance(current_effect_records,list):
        raise ValueError('이펙트 시드는 목록이어야 합니다.')
    current_seen_identifiers=set()
    for current_effect_record in current_effect_records:
        current_required_fields={'id','name','image','renderer','columns','rows','frames'}
        if not isinstance(current_effect_record,dict) or not current_required_fields<=set(current_effect_record) or set(current_effect_record)-current_required_fields-{'animation','alignment'}:
            raise ValueError('이펙트 필드 오류')
        current_asset_identifier=current_effect_record['id']
        if not isinstance(current_asset_identifier,str) or not current_asset_identifier or current_asset_identifier in current_seen_identifiers:
            raise ValueError('이펙트 ID 중복·형식 오류')
        current_seen_identifiers.add(current_asset_identifier)
        for current_path_field in ('image','animation'):
            if current_path_field in current_effect_record:
                current_path_value = current_effect_record[current_path_field]
                if (not isinstance(current_path_value, str) or not current_path_value.startswith('assets/sprites/effects/')
                        or '\\' in current_path_value or '..' in current_path_value.split('/')
                        or str(PurePosixPath(current_path_value)) != current_path_value):
                    raise ValueError('이펙트 원본 경로 오류')
        if any(type(current_effect_record[current_size_field]) is not int or current_effect_record[current_size_field]<1 for current_size_field in ('columns','rows','frames')) or current_effect_record['frames']>current_effect_record['columns']*current_effect_record['rows']:
            raise ValueError('이펙트 프레임 규격 오류')
        if 'alignment' in current_effect_record:
            if (current_effect_record['columns'],current_effect_record['rows'],current_effect_record['frames'])!=(4,2,8):raise ValueError('빌보드 발광 정렬은 4×2, 8프레임 시트입니다.')
            validate_effect_alignment_values(current_effect_record['alignment'],current_effect_record['frames'])
        current_previous_record=current_database_connection.execute('SELECT document FROM content.effect_assets WHERE asset_id=%s',(current_asset_identifier,)).fetchone()
        if current_previous_record:
            if current_previous_record['document']!=current_effect_record:raise ValueError('기존 이펙트 원장과 시드가 다릅니다.')
        else:
            current_database_connection.execute('INSERT INTO content.effect_assets(asset_id,document) VALUES(%s,%s)',(current_asset_identifier,Jsonb(current_effect_record)))


def validate_effect_alignment_values(current_alignment_values,current_frame_count):
    import math
    import re
    if not isinstance(current_alignment_values,dict) or set(current_alignment_values)!={'texture','key','alpha','target','frames'}:
        raise ValueError('발광 정렬 필드 오류')
    for current_field_name in ('texture','key'):
        if not isinstance(current_alignment_values[current_field_name],str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,95}',current_alignment_values[current_field_name]):
            raise ValueError('발광 연결 식별자 오류')
    current_alpha_value=current_alignment_values['alpha']
    if type(current_alpha_value) not in (int,float) or not math.isfinite(current_alpha_value) or not 0<=current_alpha_value<=1:
        raise ValueError('발광 밝기는 0~1입니다.')
    if not isinstance(current_alignment_values['frames'],list) or len(current_alignment_values['frames'])!=current_frame_count:
        raise ValueError('발광 프레임 수가 일치하지 않습니다.')
    for current_rectangle_values in [current_alignment_values['target'],*current_alignment_values['frames']]:
        if not isinstance(current_rectangle_values,list) or len(current_rectangle_values)!=4 or any(type(current_coordinate_value) not in (int,float) or not math.isfinite(current_coordinate_value) or not 0<=current_coordinate_value<=1 for current_coordinate_value in current_rectangle_values):
            raise ValueError('정렬 사각형은 0~1의 좌·상·우·하 좌표입니다.')
        if current_rectangle_values[0]>=current_rectangle_values[2] or current_rectangle_values[1]>=current_rectangle_values[3]:
            raise ValueError('정렬 사각형의 폭·높이는 양수여야 합니다.')
    return current_alignment_values


def hash_effect_asset_document(current_effect_document):
    import hashlib
    import json
    return hashlib.sha256(json.dumps(current_effect_document,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_new_effect_asset(current_effect_record):
    """신규 원장에 기록할 렌더러별 계약을 확인한다."""
    import re
    current_required_fields = {'id', 'name', 'image', 'renderer', 'columns', 'rows', 'frames'}
    if not isinstance(current_effect_record, dict) or not current_required_fields <= set(current_effect_record) or set(current_effect_record) - current_required_fields - {'animation', 'alignment'}:
        raise ValueError('이펙트 등록 필드 오류')
    if not isinstance(current_effect_record['id'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,95}', current_effect_record['id']):
        raise ValueError('이펙트 ID 오류')
    if not isinstance(current_effect_record['name'], str) or not 1 <= len(current_effect_record['name']) <= 200:
        raise ValueError('이펙트 이름 오류')
    current_renderer_name = current_effect_record['renderer']
    if current_renderer_name not in ('swarm', 'billboard-glow', 'fog', 'safe-barrier-aura', 'waypoint-animation'):
        raise ValueError('지원하지 않는 이펙트 렌더러입니다.')
    if any(type(current_effect_record[current_field_name]) is not int or not 1 <= current_effect_record[current_field_name] <= 256 for current_field_name in ('columns', 'rows', 'frames')) or current_effect_record['frames'] > current_effect_record['columns'] * current_effect_record['rows']:
        raise ValueError('이펙트 프레임 규격 오류')
    if current_renderer_name in ('swarm', 'billboard-glow', 'fog') and 'alignment' not in current_effect_record:
        raise ValueError('이 렌더러에는 발광 정렬이 필요합니다.')
    if current_renderer_name != 'waypoint-animation' and 'animation' in current_effect_record:
        raise ValueError('애니메이션 원본은 웨이포인트에만 사용할 수 있습니다.')
    if current_renderer_name in ('safe-barrier-aura', 'waypoint-animation') and 'alignment' in current_effect_record:
        raise ValueError('이 렌더러는 발광 정렬을 사용하지 않습니다.')
    if current_renderer_name == 'waypoint-animation' and ('animation' not in current_effect_record or (current_effect_record['columns'], current_effect_record['rows'], current_effect_record['frames']) != (4, 2, 8)):
        raise ValueError('웨이포인트는 애니메이션 원본이 있는 4×2 8프레임이어야 합니다.')
    if 'alignment' in current_effect_record:
        if (current_effect_record['columns'], current_effect_record['rows'], current_effect_record['frames']) != (4, 2, 8):
            raise ValueError('발광 정렬은 4×2 8프레임이어야 합니다.')
        validate_effect_alignment_values(current_effect_record['alignment'], current_effect_record['frames'])


def register_content_effect_asset(current_database_connection, current_effect_record, current_expected_billboard_revision=None):
    """기존 이펙트를 덮어쓰지 않고 신규 원장 세대를 기록한다."""
    validate_new_effect_asset(current_effect_record)
    if current_expected_billboard_revision is not None and (current_effect_record['renderer'] != 'billboard-glow' or type(current_expected_billboard_revision) is not int or current_expected_billboard_revision < 1):
        raise ValueError('빌보드 대상 리비전 오류')
    current_generation_record = current_database_connection.execute('SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    if current_expected_billboard_revision is not None:
        from asset_service.content_asset_store import ContentRevisionConflict
        current_billboard_record = current_database_connection.execute('SELECT asset_revision,retired FROM content.billboard_assets WHERE asset_id=%s FOR UPDATE', (current_effect_record['alignment']['texture'],)).fetchone()
        if current_billboard_record is None or current_billboard_record['retired'] or current_billboard_record['asset_revision'] != current_expected_billboard_revision:
            raise ContentRevisionConflict('빌보드 대상이 변경되었습니다. 다시 검토하세요.')
    current_existing_record = current_database_connection.execute('SELECT document FROM content.effect_assets WHERE asset_id=%s', (current_effect_record['id'],)).fetchone()
    if current_existing_record is not None:
        raise ValueError('이미 등록된 이펙트입니다. 신규 등록으로 덮어쓸 수 없습니다.')
    if 'alignment' in current_effect_record:
        current_alignment_values = validate_effect_alignment_values(current_effect_record['alignment'], current_effect_record.get('frames'))
        for current_existing_row in current_database_connection.execute('SELECT document FROM content.effect_assets'):
            current_existing_alignment = current_existing_row['document'].get('alignment')
            if current_existing_alignment is not None:
                if not isinstance(current_existing_alignment, dict) or not all(isinstance(current_existing_alignment.get(current_field_name), str) for current_field_name in ('texture', 'key')):
                    raise ValueError('기존 발광 연결 정보를 확인할 수 없습니다.')
                if any(current_existing_alignment[current_field_name] == current_alignment_values[current_field_name] for current_field_name in ('texture', 'key')):
                    raise ValueError('이미 사용 중인 발광 텍스처 또는 연결 키입니다.')
    seed_effect_asset_records(current_database_connection, [current_effect_record])
    current_next_generation = current_generation_record['current_generation'] + 1
    current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true', (current_next_generation,))
    return {'created': True, 'id': current_effect_record['id'], 'generation': current_next_generation, 'snapshot': 'pending'}


def replace_waypoint_effect_asset(current_database_connection, current_asset_identifier, current_request_payload):
    """리비전과 요청 영수증을 확인하여 웨이포인트 원장을 교체한다."""
    from uuid import UUID
    from asset_service.content_asset_store import ContentRevisionConflict
    if not isinstance(current_request_payload, dict) or set(current_request_payload) != {'expectedRevision', 'requestId', 'effect'}:
        raise ValueError('기존 리비전·요청 ID·새 이펙트가 필요합니다.')
    if not isinstance(current_request_payload['requestId'], str) or not isinstance(current_request_payload['expectedRevision'], str):
        raise ValueError('요청 ID와 revision은 문자열이어야 합니다.')
    current_request_identifier = str(UUID(current_request_payload['requestId']))
    current_next_effect = current_request_payload['effect']
    validate_new_effect_asset(current_next_effect)
    if not isinstance(current_next_effect, dict) or current_next_effect.get('renderer') != 'waypoint-animation' or (current_next_effect.get('columns'), current_next_effect.get('rows'), current_next_effect.get('frames')) != (4, 2, 8):
        raise ValueError('웨이포인트는 4×2 8프레임이어야 합니다.')
    current_request_hash = hash_effect_asset_document({'asset': current_asset_identifier, **current_request_payload})
    current_generation_record = current_database_connection.execute('SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    current_receipt_record = current_database_connection.execute('SELECT request_sha256,response_values FROM content.change_receipts WHERE request_id=%s', (current_request_identifier,)).fetchone()
    if current_receipt_record:
        if current_receipt_record['request_sha256'] != current_request_hash:
            raise ContentRevisionConflict('요청 ID의 입력이 변경되었습니다.')
        return current_receipt_record['response_values']
    current_before_record = current_database_connection.execute('SELECT document FROM content.effect_assets WHERE asset_id=%s FOR UPDATE', (current_asset_identifier,)).fetchone()
    if not current_before_record or current_before_record['document'].get('renderer') != 'waypoint-animation':
        raise ValueError('기존 웨이포인트 이펙트가 없습니다.')
    current_before_values = current_before_record['document']
    if hash_effect_asset_document(current_before_values) != current_request_payload['expectedRevision']:
        raise ContentRevisionConflict('이펙트가 변경되었습니다. 다시 확인하세요.')
    if current_database_connection.execute('SELECT 1 FROM content.effect_assets WHERE asset_id=%s', (current_next_effect.get('id'),)).fetchone():
        raise ValueError('새 이펙트 ID가 이미 있습니다.')
    seed_effect_asset_records(current_database_connection, [current_next_effect])
    current_database_connection.execute('DELETE FROM content.effect_assets WHERE asset_id=%s', (current_asset_identifier,))
    current_next_generation = current_generation_record['current_generation'] + 1
    current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true', (current_next_generation,))
    current_result_values = {'saved': True, 'id': current_next_effect['id'], 'replaced': current_asset_identifier, 'generation': current_next_generation, 'snapshot': 'pending'}
    current_database_connection.execute('INSERT INTO content.change_receipts(request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values) VALUES(%s,%s,%s,%s,%s,%s,%s)', (current_request_identifier, current_request_hash, current_next_generation, 'management-gateway', Jsonb(current_before_values), Jsonb(current_next_effect), Jsonb(current_result_values)))
    return current_result_values


def save_effect_alignment_values(current_database_connection,current_asset_identifier,current_request_payload):
    from asset_service.content_asset_store import ContentRevisionConflict
    from uuid import UUID
    if set(current_request_payload)!={'expectedRevision','requestId','alignment'}:
        raise ValueError('expectedRevision·requestId·alignment가 필요합니다.')
    if not isinstance(current_request_payload['requestId'], str) or not isinstance(current_request_payload['expectedRevision'], str):
        raise ValueError('요청 ID와 revision은 문자열이어야 합니다.')
    current_request_identifier=str(UUID(current_request_payload['requestId']))
    current_generation_record=current_database_connection.execute('SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    current_request_hash=hash_effect_asset_document({'asset':current_asset_identifier,**current_request_payload})
    current_receipt_record=current_database_connection.execute('SELECT request_sha256,response_values FROM content.change_receipts WHERE request_id=%s',(current_request_identifier,)).fetchone()
    if current_receipt_record:
        if current_receipt_record['request_sha256']!=current_request_hash:raise ContentRevisionConflict('요청 ID의 입력이 변경되었습니다.')
        return current_receipt_record['response_values']
    current_asset_record=current_database_connection.execute('SELECT document FROM content.effect_assets WHERE asset_id=%s FOR UPDATE',(current_asset_identifier,)).fetchone()
    if not current_asset_record:raise ValueError('등록된 이펙트가 아닙니다.')
    current_before_values=current_asset_record['document']
    if hash_effect_asset_document(current_before_values)!=current_request_payload['expectedRevision']:
        raise ContentRevisionConflict('이펙트가 변경되었습니다. 다시 조회하세요.')
    if 'alignment' not in current_before_values:raise ValueError('좌표 검수용으로 등록되지 않은 이펙트입니다.')
    current_alignment_values=validate_effect_alignment_values(current_request_payload['alignment'],current_before_values['frames'])
    if any(current_alignment_values[current_field_name]!=current_before_values['alignment'][current_field_name] for current_field_name in ('texture','key')):
        raise ValueError('연결 에셋·텍스처 변경은 별도 등록이 필요합니다.')
    current_after_values={**current_before_values,'alignment':current_alignment_values}
    current_next_generation=current_generation_record['current_generation']+1
    current_response_values={'saved':True,'revision':hash_effect_asset_document(current_after_values),'generation':current_next_generation,'snapshot':'pending'}
    current_database_connection.execute('UPDATE content.effect_assets SET document=%s WHERE asset_id=%s',(Jsonb(current_after_values),current_asset_identifier))
    current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true',(current_next_generation,))
    current_database_connection.execute('INSERT INTO content.change_receipts(request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values) VALUES(%s,%s,%s,%s,%s,%s,%s)',(current_request_identifier,current_request_hash,current_next_generation,'management-gateway',Jsonb(current_before_values),Jsonb(current_after_values),Jsonb(current_response_values)))
    return current_response_values
