"""높은 교량의 독립 보행면. 아래 지면은 보존하지만 상하 동시 통행은 지원하지 않는다."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

BRIDGE_MAXIMUM_ELEVATION = 20

class BridgeCellPosition(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    column: Annotated[int, Field(ge=0)]
    row: Annotated[int, Field(ge=0)]

class TerrainBridgeDefinition(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    id: Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]*$')]
    start: BridgeCellPosition
    end: BridgeCellPosition
    elevation: Annotated[int, Field(ge=1, le=BRIDGE_MAXIMUM_ELEVATION)]
    material: Literal['timber', 'stone', 'farm-timber']

    @model_validator(mode='after')
    def validate_bridge_span(current_bridge_record):
        current_column_delta = current_bridge_record.end.column-current_bridge_record.start.column
        current_row_delta = current_bridge_record.end.row-current_bridge_record.start.row
        if (current_column_delta == 0) == (current_row_delta == 0) or abs(current_column_delta)+abs(current_row_delta)<2:
            raise ValueError('교량은 중간 상판이 있는 직선 구간이어야 합니다.')
        return current_bridge_record


def enumerate_bridge_cells(current_bridge_record):
    current_start_cell = current_bridge_record['start']
    current_end_cell = current_bridge_record['end']
    current_column_delta = current_end_cell['column']-current_start_cell['column']
    current_row_delta = current_end_cell['row']-current_start_cell['row']
    current_span_length = abs(current_column_delta)+abs(current_row_delta)
    current_column_step = (current_column_delta>0)-(current_column_delta<0)
    current_row_step = (current_row_delta>0)-(current_row_delta<0)
    return [dict(column=current_start_cell['column']+current_column_step*current_step_index,
                 row=current_start_cell['row']+current_row_step*current_step_index)
            for current_step_index in range(current_span_length+1)]


def find_bridge_surface(current_cell_position, current_map_definition):
    return next((current_bridge_record for current_bridge_record in current_map_definition.get('bridges', [])
                 if current_cell_position in enumerate_bridge_cells(current_bridge_record)[1:-1]), None)


def resolve_bridge_step(current_start_cell, current_end_cell, current_map_definition):
    for current_cell_position in (current_start_cell, current_end_cell):
        current_bridge_record = find_bridge_surface(current_cell_position, current_map_definition)
        if current_bridge_record:
            current_bridge_cells = enumerate_bridge_cells(current_bridge_record)
            if current_start_cell not in current_bridge_cells or current_end_cell not in current_bridge_cells:
                return False
    return True


def validate_bridge_geometry(current_bridge_records, current_height_rows, current_column_count, current_row_count):
    current_used_identifiers = set()
    current_used_positions = set()
    for current_bridge_record in current_bridge_records:
        if current_bridge_record.id in current_used_identifiers:
            raise ValueError('교량 ID가 중복되었습니다.')
        current_used_identifiers.add(current_bridge_record.id)
        current_bridge_cells = enumerate_bridge_cells(current_bridge_record.model_dump())
        for current_cell_index, current_cell_position in enumerate(current_bridge_cells):
            current_column_index, current_row_index = current_cell_position['column'], current_cell_position['row']
            if current_column_index>=current_column_count or current_row_index>=current_row_count:
                raise ValueError('교량 좌표가 맵 범위를 벗어났습니다.')
            current_position_pair = (current_column_index,current_row_index)
            if current_position_pair in current_used_positions:
                raise ValueError('교량 구간이나 진입점이 겹칩니다.')
            current_used_positions.add(current_position_pair)
            current_ground_height = int(current_height_rows[current_row_index][current_column_index])
            if current_cell_index in (0,len(current_bridge_cells)-1):
                if abs(current_ground_height-current_bridge_record.elevation)>1:
                    raise ValueError('교량 진입점과 상판의 높이 차이는 1 이하여야 합니다.')
            elif current_ground_height>=current_bridge_record.elevation:
                raise ValueError('교량 상판은 아래 지면보다 높아야 합니다.')
