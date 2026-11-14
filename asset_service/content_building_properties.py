"""건물 앵커와 선택적 표시 크기 보정의 원장 계약."""
import math

BUILDING_SHADOW_DEFAULT_VALUES = {'shadowShape':'circle','shadowOffsetColumn':0,'shadowOffsetRow':0,'shadowScaleX':1,'shadowScaleY':1}
BUILDING_SHADOW_PROPERTY_FIELDS = frozenset(BUILDING_SHADOW_DEFAULT_VALUES)

BUILDING_DISPLAY_ADJUSTMENT_FIELDS = frozenset(('displayWidthAdjustment', 'displayHeightAdjustment'))


def validate_building_property_values(current_property_values):
    if (not isinstance(current_property_values, dict)
            or not {'anchorX', 'anchorY'} <= current_property_values.keys()
            or set(current_property_values) - {'anchorX', 'anchorY'} - BUILDING_DISPLAY_ADJUSTMENT_FIELDS - BUILDING_SHADOW_PROPERTY_FIELDS
            or any(type(current_numeric_value) not in (int, float) or not math.isfinite(current_numeric_value)
                   for current_field_name,current_numeric_value in current_property_values.items() if current_field_name!='shadowShape')):
        raise ValueError('건물 공용 앵커·표시 크기 보정 형식 오류')
    if 'shadowShape' in current_property_values and current_property_values['shadowShape'] not in ('circle','rectangle'):
        raise ValueError('그림자 형태는 circle 또는 rectangle이어야 합니다.')
    if set(current_property_values) & BUILDING_SHADOW_PROPERTY_FIELDS and not BUILDING_SHADOW_PROPERTY_FIELDS <= set(current_property_values):
        raise ValueError('그림자 형태·위치·크기를 함께 지정하세요.')
    for current_field_name in ('shadowOffsetColumn','shadowOffsetRow'):
        if current_field_name in current_property_values and not -4<=current_property_values[current_field_name]<=4:raise ValueError('그림자 이동은 -4~4타일이어야 합니다.')
    for current_field_name in ('shadowScaleX','shadowScaleY'):
        if current_field_name in current_property_values and not .1<=current_property_values[current_field_name]<=4:raise ValueError('그림자 크기는 0.1~4배여야 합니다.')
    return {current_field_name: current_property_values[current_field_name]
            for current_field_name in BUILDING_DISPLAY_ADJUSTMENT_FIELDS | BUILDING_SHADOW_PROPERTY_FIELDS if current_field_name in current_property_values}


def decode_building_property_record(current_building_record):
    return {'anchorX': float(current_building_record['anchor_x']), 'anchorY': float(current_building_record['anchor_y']),
            **current_building_record['display_adjustments']}
