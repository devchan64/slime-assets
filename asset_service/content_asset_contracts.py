"""빌보드 DB API의 엄격한 공용 속성 계약."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

ContentFiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]
ContentAssetIdentifier = Annotated[str, Field(pattern=r'^[a-z][a-z0-9-]*$', max_length=180)]


class BillboardPropertyValues(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    anchorX: ContentFiniteNumber
    anchorY: ContentFiniteNumber
    groundFootprintColumns: Annotated[int, Field(ge=1, le=8)]
    groundFootprintRows: Annotated[int, Field(ge=1, le=8)]
    displayHeight: Annotated[float, Field(gt=0, le=1024, allow_inf_nan=False)]
    billboardBlocksMovement: bool
    billboardPlacementAnchor: Literal['top', 'bottom'] | None = None
    billboardAnchorMode: Literal['tile-bottom-center', 'tile-intersection'] | None = None
    shadowOffsetColumn: Annotated[float, Field(ge=-4, le=4, allow_inf_nan=False)]
    shadowOffsetRow: Annotated[float, Field(ge=-4, le=4, allow_inf_nan=False)]
    shadowScaleX: Annotated[float, Field(ge=0.001, le=4, allow_inf_nan=False)]
    shadowScaleY: Annotated[float, Field(ge=0.001, le=4, allow_inf_nan=False)]
    shadowCoordinateVersion: Literal[2]
    shadowSizeVersion: Literal[2]



class BillboardSaveRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    requestId: Annotated[str, Field(min_length=1, max_length=180)]
    expectedRevision: Annotated[int, Field(ge=1)]
    properties: BillboardPropertyValues


BILLBOARD_PROPERTY_COLUMN_NAMES = {
    'anchorX': 'anchor_x', 'anchorY': 'anchor_y',
    'groundFootprintColumns': 'footprint_columns', 'groundFootprintRows': 'footprint_rows',
    'displayHeight': 'display_height', 'billboardBlocksMovement': 'blocks_movement',
    'billboardPlacementAnchor': 'placement_anchor', 'billboardAnchorMode': 'anchor_mode',
    'shadowOffsetColumn': 'shadow_offset_column', 'shadowOffsetRow': 'shadow_offset_row',
    'shadowScaleX': 'shadow_scale_column', 'shadowScaleY': 'shadow_scale_row',
    'shadowCoordinateVersion': 'shadow_coordinate_version', 'shadowSizeVersion': 'shadow_size_version',
}


def decode_billboard_property_record(current_database_record):
    current_property_values = {current_field_name: current_database_record[current_column_name]
        for current_field_name, current_column_name in BILLBOARD_PROPERTY_COLUMN_NAMES.items()}
    for current_field_name in ('anchorX', 'anchorY', 'displayHeight', 'shadowOffsetColumn',
            'shadowOffsetRow', 'shadowScaleX', 'shadowScaleY'):
        current_property_values[current_field_name] = float(current_property_values[current_field_name])
    return BillboardPropertyValues.model_validate(current_property_values).model_dump(exclude_none=True)
