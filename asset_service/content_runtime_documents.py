"""파일 경로 계약을 DB의 정규화된 맵·공용 속성 조회에 연결한다."""
import re
from asset_service.content_asset_contracts import decode_billboard_property_record
from asset_service.content_map_documents import read_map_document_records


def read_shared_property_document(current_database_connection):
    current_property_records=current_database_connection.execute('SELECT * FROM content.billboard_properties ORDER BY asset_id').fetchall()
    current_property_catalog={current_property_record['asset_id']:decode_billboard_property_record(current_property_record) for current_property_record in current_property_records}
    current_building_records=current_database_connection.execute('SELECT * FROM content.building_properties ORDER BY asset_id').fetchall()
    for current_building_record in current_building_records:
        if current_building_record['asset_id'] in current_property_catalog:raise ValueError('빌보드·건물 공용 ID 중복')
        current_property_catalog[current_building_record['asset_id']]={'anchorX':float(current_building_record['anchor_x']),'anchorY':float(current_building_record['anchor_y'])}
    return {'managementId':'map.sprite-display-properties','data':current_property_catalog}


def read_runtime_map_document(current_database_connection,current_document_path):
    if current_document_path=='sprite-display-properties.yaml':return read_shared_property_document(current_database_connection)
    return read_map_document_records(current_database_connection,current_document_path)


def read_review_document_bundle(current_database_connection,current_map_identifier):
    if current_map_identifier is not None and not re.fullmatch(r'[a-z][a-z0-9-]*', current_map_identifier):
        raise ValueError('맵 ID 형식 오류')
    current_document_paths=['field_tiles/index.yaml','city_layouts/index.yaml','sprite-display-properties.yaml']
    if current_map_identifier is not None:
        current_document_paths.append('map_names/'+current_map_identifier+'.yaml')
        current_city_index=read_map_document_records(current_database_connection,'city_layouts/index.yaml')['data']
        if current_map_identifier in current_city_index['includes']['layouts']:
            current_document_paths.extend(['city_layouts/'+current_map_identifier+'.yaml'])
        else:
            current_document_paths.extend([current_directory_name+'/'+current_map_identifier+'.yaml'
                for current_directory_name in ('field_tiles','terrain/maps','map_spawns','field_waypoints')])
            current_document_paths.append('field_tiles/codes.yaml')
    # 출입구·웨이포인트의 연결 대상 이름도 같은 DB 스냅샷에서 읽는다.
    current_document_paths.extend(current_name_record['document_path'] for current_name_record in
        current_database_connection.execute("SELECT document_path FROM content.map_documents WHERE document_path LIKE 'map_names/%' ORDER BY document_path").fetchall())
    return {current_document_path:read_runtime_map_document(current_database_connection,current_document_path) for current_document_path in current_document_paths}


def read_billboard_image_manifest(current_database_connection,current_texture_keys):
    """선택 맵의 현재 이미지 버전을 조회하고 내부 원본 경로는 제외한다."""
    current_image_records=current_database_connection.execute("""
        SELECT billboard.asset_id,billboard.asset_type,image.published_path,image.image_sha256,
            image.image_width,image.image_height
        FROM content.billboard_assets AS billboard
        JOIN content.image_versions AS image ON image.image_id=billboard.image_id
            AND image.image_version=billboard.image_version
        WHERE billboard.asset_id=ANY(%s) AND NOT billboard.retired
        ORDER BY billboard.asset_id
    """,(sorted(set(current_texture_keys)),)).fetchall()
    return {current_image_record['asset_id']:{
        'url':current_image_record['published_path'],'sha256':current_image_record['image_sha256'],
        'assetType':current_image_record['asset_type'],
        'width':current_image_record['image_width'],'height':current_image_record['image_height']}
        for current_image_record in current_image_records}


def collect_document_texture_keys(current_document_value):
    """맵 객체와 건물에서 사용하는 텍스처를 수집한다."""
    current_texture_keys=set()
    if isinstance(current_document_value,dict):
        for current_field_name,current_field_value in current_document_value.items():
            if current_field_name=='textureKey' and isinstance(current_field_value,str):
                current_texture_keys.add(current_field_value)
            else:
                current_texture_keys.update(collect_document_texture_keys(current_field_value))
    elif isinstance(current_document_value,list):
        for current_child_value in current_document_value:
            current_texture_keys.update(collect_document_texture_keys(current_child_value))
    return current_texture_keys
