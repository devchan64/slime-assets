"""맵 지형·식생의 정적 색상 보정 계약."""
import re


def validate_terrain_ambient_tint(current_map_record):
    if 'ambientTint' not in current_map_record:
        return
    current_ambient_color=current_map_record['ambientTint']
    if not isinstance(current_ambient_color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',current_ambient_color):
        raise ValueError('ambientTint는 #RRGGBB 색상이어야 합니다.')
