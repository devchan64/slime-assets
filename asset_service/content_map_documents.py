"""맵 문서를 셀·배치 테이블로 분해하고 기존 API 계약으로 복원한다."""
from copy import deepcopy
import re
from pathlib import PurePosixPath
from psycopg.types.json import Jsonb

MAP_GRID_FIELD_NAMES = ('terrainRows','terrain','elevations')
MAP_SHARED_PROPERTY_FIELDS = frozenset(('anchorX','anchorY','groundFootprintColumns','groundFootprintRows',
    'displayHeight','billboardBlocksMovement','billboardPlacementAnchor','billboardAnchorMode',
    'shadowOffsetColumn','shadowOffsetRow','shadowScaleX','shadowScaleY','shadowCoordinateVersion','shadowSizeVersion'))


PUBLIC_MAP_ROOT_DOCUMENTS = frozenset(('world-map.yaml', 'battlefield_encounters.yaml'))
PUBLIC_MAP_DOCUMENT_ROOTS = frozenset(('map_names','field_tiles','map_spawns','city_layouts','field_waypoints','terrain'))


def validate_public_map_path(current_document_path):
    if not isinstance(current_document_path, str) or not current_document_path:
        raise ValueError('맵 문서 경로는 문자열이어야 합니다.')
    current_relative_path = PurePosixPath(current_document_path)
    if (current_relative_path.is_absolute() or '..' in current_relative_path.parts
            or str(current_relative_path) != current_document_path or '\\' in current_document_path
            or current_relative_path.suffix != '.yaml'
            or (current_document_path not in PUBLIC_MAP_ROOT_DOCUMENTS and
                (len(current_relative_path.parts) < 2 or current_relative_path.parts[0] not in PUBLIC_MAP_DOCUMENT_ROOTS))):
        raise ValueError('공개 맵 문서 허용 경로가 아닙니다: ' + current_document_path)


def split_map_document_records(current_document_path,current_document_record):
    validate_public_map_path(current_document_path)
    if not isinstance(current_document_record,dict) or set(current_document_record)!={'managementId','data'}:
        raise ValueError('맵 문서는 managementId·data만 허용합니다.')
    current_document_data=deepcopy(current_document_record['data'])
    if not isinstance(current_document_data,dict):
        raise ValueError('맵 문서 data는 객체여야 합니다.')
    current_grid_records=[]
    current_grid_formats={}
    for current_grid_name in (*MAP_GRID_FIELD_NAMES,'rows'):
        current_grid_values=current_document_data.get(current_grid_name)
        if not isinstance(current_grid_values,list):continue
        if not current_grid_values or not all(isinstance(current_row_values,(str,list)) for current_row_values in current_grid_values):
            raise ValueError('맵 격자 형식 오류: '+current_document_path+':'+current_grid_name)
        current_row_format='string' if isinstance(current_grid_values[0],str) else 'list'
        current_grid_width=len(current_grid_values[0])
        if not current_grid_width:raise ValueError('빈 맵 격자')
        for current_row_index,current_row_values in enumerate(current_grid_values):
            if len(current_row_values)!=current_grid_width or isinstance(current_row_values,str)!=(current_row_format=='string'):
                raise ValueError('맵 격자의 행 형식·폭 불일치')
            for current_column_index,current_cell_value in enumerate(current_row_values):
                if type(current_cell_value) not in (str,int):raise ValueError('맵 셀은 문자열 또는 정수여야 합니다.')
                current_grid_records.append((current_grid_name,current_column_index,current_row_index,current_cell_value))
        current_grid_formats[current_grid_name]={'format':current_row_format,'columns':current_grid_width,'rows':len(current_grid_values)}
        del current_document_data[current_grid_name]
    current_object_present='objects' in current_document_data
    current_object_values=current_document_data.pop('objects',[])
    if not isinstance(current_object_values,list):raise ValueError('맵 objects는 목록이어야 합니다.')
    current_object_records=[]
    current_object_identifiers=set()
    for current_object_index,current_object_record in enumerate(current_object_values):
        if not isinstance(current_object_record,dict) or MAP_SHARED_PROPERTY_FIELDS & current_object_record.keys():
            raise ValueError('맵별 공용 속성 재정의 금지')
        current_allowed_fields={'id','kind','position','textureKey','tileAnchor','frameSize'}
        if not {'id','kind','position'} <= current_object_record.keys() or set(current_object_record)-current_allowed_fields:
            raise ValueError('맵 배치 필수 필드 누락 또는 허용되지 않은 필드')
        if current_object_record['kind'] not in ('ward-tower','decoration','billboard'):
            raise ValueError('맵 배치 kind 오류')
        if current_object_record.get('tileAnchor','center') not in ('back','center','front'):
            raise ValueError('맵 배치 tileAnchor 오류')
        current_texture_value=current_object_record.get('textureKey')
        if current_object_record['kind']!='ward-tower' and (not isinstance(current_texture_value,str) or not re.fullmatch(r'[a-z][a-z0-9-]*',current_texture_value)):
            raise ValueError('맵 배치 textureKey 오류')
        current_object_identifier=current_object_record['id']
        if not isinstance(current_object_identifier,str) or not re.fullmatch(r'[a-z][a-z0-9-]*',current_object_identifier):
            raise ValueError('맵 배치 ID 오류')
        if current_object_identifier in current_object_identifiers:raise ValueError('맵 오브젝트 ID 중복')
        current_object_identifiers.add(current_object_identifier)
        current_position_record=current_object_record['position']
        if not isinstance(current_position_record,dict) or set(current_position_record)!={'column','row'} or any(type(current_position_value) is not int or current_position_value<0 for current_position_value in current_position_record.values()):
            raise ValueError('맵 배치 좌표 오류')
        for current_grid_format in current_grid_formats.values():
            if current_position_record['column']>=current_grid_format['columns'] or current_position_record['row']>=current_grid_format['rows']:
                raise ValueError('맵 배치 좌표가 격자 범위를 벗어났습니다.')
        current_object_metadata={current_field_name:current_field_value for current_field_name,current_field_value in current_object_record.items() if current_field_name not in ('id','position','textureKey')}
        current_object_records.append((current_object_identifier,current_object_index,current_object_record.get('textureKey'),
            current_position_record['column'],current_position_record['row'],current_object_metadata))
    return current_document_data,current_grid_formats,current_grid_records,current_object_present,current_object_records


def insert_map_document_records(current_database_connection,current_document_path,current_document_record):
    current_metadata_record,current_grid_formats,current_grid_records,current_object_present,current_object_records=split_map_document_records(current_document_path,current_document_record)
    for current_object_record in current_object_records:
        if current_object_record[2] and current_database_connection.execute('SELECT 1 FROM content.billboard_assets WHERE asset_id=%s AND retired',(current_object_record[2],)).fetchone():
            raise ValueError('폐기된 빌보드는 배치할 수 없습니다: '+current_object_record[2])
    current_database_connection.execute('INSERT INTO content.map_documents VALUES(%s,%s,1,%s,%s,%s)',
        (current_document_path,current_document_record['managementId'],Jsonb(current_metadata_record),Jsonb(current_grid_formats),current_object_present))
    with current_database_connection.cursor() as current_database_cursor:
        current_database_cursor.executemany('INSERT INTO content.map_grid_cells VALUES(%s,%s,%s,%s,%s)',
            [(current_document_path,current_grid_name,current_column_index,current_row_index,Jsonb(current_cell_value))
             for current_grid_name,current_column_index,current_row_index,current_cell_value in current_grid_records])
        current_database_cursor.executemany('INSERT INTO content.map_object_records VALUES(%s,%s,%s,%s,%s,%s,%s)',
            [(current_document_path,*current_object_record[:-1],Jsonb(current_object_record[-1])) for current_object_record in current_object_records])


def read_map_document_records(current_database_connection,current_document_path):
    validate_public_map_path(current_document_path)
    current_document_record=current_database_connection.execute('SELECT * FROM content.map_documents WHERE document_path=%s',(current_document_path,)).fetchone()
    if current_document_record is None:raise ValueError('DB 맵 문서가 없습니다: '+current_document_path)
    current_document_data=deepcopy(current_document_record['document_metadata'])
    for current_grid_name,current_grid_format in current_document_record['grid_formats'].items():
        current_grid_rows=[[None]*current_grid_format['columns'] for current_row_index in range(current_grid_format['rows'])]
        current_cell_records=current_database_connection.execute('SELECT * FROM content.map_grid_cells WHERE document_path=%s AND grid_field=%s ORDER BY tile_row,tile_column',
            (current_document_path,current_grid_name)).fetchall()
        if len(current_cell_records)!=current_grid_format['columns']*current_grid_format['rows']:
            raise ValueError('DB 맵 격자 셀 누락')
        for current_cell_record in current_cell_records:
            current_grid_rows[current_cell_record['tile_row']][current_cell_record['tile_column']]=current_cell_record['cell_value']
        current_document_data[current_grid_name]=[''.join(current_row_values) for current_row_values in current_grid_rows] if current_grid_format['format']=='string' else current_grid_rows
    if current_document_record['object_field_present']:
        current_object_records=current_database_connection.execute('SELECT * FROM content.map_object_records WHERE document_path=%s ORDER BY object_order',(current_document_path,)).fetchall()
        current_document_data['objects']=[]
        for current_object_record in current_object_records:
            current_object_values={'id':current_object_record['object_id'],
                'position':{'column':current_object_record['tile_column'],'row':current_object_record['tile_row']},**current_object_record['object_metadata']}
            if current_object_record['asset_id'] is not None:current_object_values['textureKey']=current_object_record['asset_id']
            current_document_data['objects'].append(current_object_values)
    return {'managementId':current_document_record['management_id'],'data':current_document_data}
