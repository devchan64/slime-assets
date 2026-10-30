"""지형 발광·나방의 버전 1 데이터 계약. 통행·점유 판정과 분리한다."""
import math
import re

TERRAIN_EFFECT_MAXIMUM_COUNT = 128
TERRAIN_EFFECT_REQUIRED_FIELDS = {'version', 'id', 'kind', 'color', 'radius', 'intensity', 'periodMs', 'height', 'seed'}
TERRAIN_EFFECT_NUMERIC_LIMITS = {'radius': (0.05, 8), 'intensity': (0, 1), 'periodMs': (250, 120000), 'height': (0, 1024), 'seed': (0, 2147483647)}


def validate_terrain_effect_records(current_effect_records, current_column_count, current_row_count, current_object_records=None):
    if not isinstance(current_effect_records, list) or len(current_effect_records) > TERRAIN_EFFECT_MAXIMUM_COUNT:
        raise ValueError('지형 이펙트는 최대 128개 목록이어야 합니다.')
    current_seen_identifiers = set()
    current_object_lookup = {current_object_record['id']: current_object_record for current_object_record in (current_object_records or [])}
    for current_effect_record in current_effect_records:
        if not isinstance(current_effect_record, dict):
            raise ValueError('지형 이펙트는 객체여야 합니다.')
        current_effect_kind = current_effect_record.get('kind')
        current_allowed_fields = TERRAIN_EFFECT_REQUIRED_FIELDS | {'position', 'objectId'} | ({'count'} if current_effect_kind in ('moths', 'fireflies') else set())
        if not TERRAIN_EFFECT_REQUIRED_FIELDS <= current_effect_record.keys() or current_effect_record.keys() - current_allowed_fields:
            raise ValueError('지형 이펙트 필수·알 수 없는 필드 오류')
        if type(current_effect_record['version']) is not int or current_effect_record['version'] not in (1, 2) or current_effect_kind not in ('glow', 'moths', 'fireflies'):
            raise ValueError('지원하지 않는 지형 이펙트 버전·종류')
        if current_effect_record['version'] == 2:
            if current_effect_kind not in ('moths', 'fireflies') or current_effect_record.get('count') != (3 if current_effect_kind == 'moths' else 7):
                raise ValueError('시트 이펙트는 나방 3마리 또는 반딧불 7마리입니다.')
        elif current_effect_kind == 'fireflies':
            raise ValueError('반딧불 시트는 버전 2가 필요합니다.')
        current_effect_identifier = current_effect_record['id']
        if not isinstance(current_effect_identifier, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,95}', current_effect_identifier) or current_effect_identifier in current_seen_identifiers:
            raise ValueError('지형 이펙트 ID 형식·중복 오류')
        current_seen_identifiers.add(current_effect_identifier)
        if not isinstance(current_effect_record['color'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', current_effect_record['color']):
            raise ValueError('지형 이펙트 색상은 #RRGGBB입니다.')
        for current_field_name, (current_minimum_value, current_maximum_value) in TERRAIN_EFFECT_NUMERIC_LIMITS.items():
            current_field_value = current_effect_record[current_field_name]
            if type(current_field_value) not in (int, float) or not math.isfinite(current_field_value) or not current_minimum_value <= current_field_value <= current_maximum_value:
                raise ValueError('지형 이펙트 범위 오류: ' + current_field_name)
        if type(current_effect_record['seed']) is not int:
            raise ValueError('지형 이펙트 seed는 정수입니다.')
        if current_effect_kind in ('moths', 'fireflies') and (type(current_effect_record.get('count')) is not int or not 1 <= current_effect_record['count'] <= 24):
            raise ValueError('나방 수는 1~24 정수입니다.')
        if ('position' in current_effect_record) == ('objectId' in current_effect_record):
            raise ValueError('지형 이펙트는 position 또는 objectId 중 하나가 필요합니다.')
        if 'objectId' in current_effect_record:
            if not isinstance(current_effect_record['objectId'], str) or not current_effect_record['objectId']:
                raise ValueError('지형 이펙트 연결 ID 오류')
            if current_object_records is None:
                continue  # 분리된 지형 문서는 맵 합성 시 참조를 검사한다.
            if current_effect_record['objectId'] not in current_object_lookup:
                raise ValueError('지형 이펙트 연결 대상이 없습니다: ' + current_effect_record['objectId'])
            current_cell_position = current_object_lookup[current_effect_record['objectId']]['position']
        else:
            current_cell_position = current_effect_record['position']
        if not isinstance(current_cell_position, dict) or set(current_cell_position) != {'column', 'row'} or any(type(current_cell_position[current_axis_name]) is not int for current_axis_name in ('column', 'row')):
            raise ValueError('지형 이펙트 좌표는 열·행 정수입니다.')
        if not 0 <= current_cell_position['column'] < current_column_count or not 0 <= current_cell_position['row'] < current_row_count:
            raise ValueError('지형 이펙트 좌표가 맵 밖입니다: ' + current_effect_identifier)
    return current_effect_records
