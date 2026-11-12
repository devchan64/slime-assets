"""건물 앵커와 선택적 표시 크기 보정의 원장 계약."""
import math

BUILDING_DISPLAY_ADJUSTMENT_FIELDS = frozenset(('displayWidthAdjustment', 'displayHeightAdjustment'))


def validate_building_property_values(current_property_values):
    if (not isinstance(current_property_values, dict)
            or not {'anchorX', 'anchorY'} <= current_property_values.keys()
            or set(current_property_values) - {'anchorX', 'anchorY'} - BUILDING_DISPLAY_ADJUSTMENT_FIELDS
            or any(type(current_numeric_value) not in (int, float) or not math.isfinite(current_numeric_value)
                   for current_numeric_value in current_property_values.values())):
        raise ValueError('건물 공용 앵커·표시 크기 보정 형식 오류')
    return {current_field_name: current_property_values[current_field_name]
            for current_field_name in BUILDING_DISPLAY_ADJUSTMENT_FIELDS if current_field_name in current_property_values}


def decode_building_property_record(current_building_record):
    return {'anchorX': float(current_building_record['anchor_x']), 'anchorY': float(current_building_record['anchor_y']),
            **current_building_record['display_adjustments']}
