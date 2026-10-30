"""공용 속성과 이미지 참조를 결합하는 콘텐츠 조회."""
from asset_service.content_asset_contracts import decode_billboard_property_record

BILLBOARD_QUERY_SELECT = '''SELECT billboard.*,properties.*,image.source_path,image.image_sha256,
    image.image_width,image.image_height,image.published_path,
    COALESCE((SELECT jsonb_agg(jsonb_build_object('path','assets/maps/'||usage.document_path,
        'texture_key_occurrences',usage.occurrences) ORDER BY usage.document_path)
        FROM (SELECT placement.document_path,count(*) AS occurrences
            FROM content.map_object_records AS placement WHERE placement.asset_id=billboard.asset_id
            GROUP BY placement.document_path) AS usage),'[]'::jsonb) AS map_references
    FROM content.billboard_assets AS billboard
    JOIN content.billboard_properties AS properties USING(asset_id)
    JOIN content.image_versions AS image ON image.image_id=billboard.image_id
        AND image.image_version=billboard.image_version'''


def build_billboard_response_record(current_database_record):
    return {'assetId':current_database_record['asset_id'],'assetType':current_database_record['asset_type'],
        'name':current_database_record['display_name'],'revision':current_database_record['asset_revision'],
        'retired':current_database_record['retired'],
        'image':{'imageId':current_database_record['image_id'],'version':current_database_record['image_version'],
            'path':current_database_record['source_path'],'sha256':current_database_record['image_sha256'],
            'width':current_database_record['image_width'],'height':current_database_record['image_height'],
            'publishedPath':current_database_record['published_path']},
        'properties':decode_billboard_property_record(current_database_record),
        'metadata':{**current_database_record['metadata'],'mapReferences':current_database_record['map_references']}}


def read_content_generation_state(current_database_connection):
    current_generation_record=current_database_connection.execute('SELECT * FROM content.generations WHERE singleton=true').fetchone()
    if current_generation_record is None:
        raise ValueError('콘텐츠 DB 초기화가 필요합니다.')
    return {'generation':current_generation_record['current_generation'],
        'snapshotGeneration':current_generation_record['snapshot_generation']}


def list_billboard_asset_records(current_database_connection,current_asset_type,current_page_cursor='',current_page_limit=100):
    if type(current_page_limit) is not int or not 1 <= current_page_limit <= 500:
        raise ValueError('페이지 크기는 1~500이어야 합니다.')
    if not current_database_connection.execute('SELECT 1 FROM content.asset_types WHERE asset_type=%s',(current_asset_type,)).fetchone():
        raise ValueError('등록된 에셋 타입을 선택하세요. 전체 조회는 지원하지 않습니다.')
    current_asset_records=current_database_connection.execute(BILLBOARD_QUERY_SELECT+'''
        WHERE billboard.asset_type=%s AND billboard.asset_id>%s AND NOT billboard.retired
        ORDER BY billboard.asset_id LIMIT %s''',(current_asset_type,current_page_cursor,current_page_limit+1)).fetchall()
    current_page_records=current_asset_records[:current_page_limit]
    return {'assets':[build_billboard_response_record(current_asset_record) for current_asset_record in current_page_records],
        'nextCursor':current_page_records[-1]['asset_id'] if len(current_asset_records)>current_page_limit else None,
        **read_content_generation_state(current_database_connection)}


def read_billboard_asset_record(current_database_connection,current_asset_identifier):
    current_asset_record=current_database_connection.execute(BILLBOARD_QUERY_SELECT+' WHERE billboard.asset_id=%s',
        (current_asset_identifier,)).fetchone()
    if current_asset_record is None:
        raise ValueError('빌보드 ID가 없습니다: '+current_asset_identifier)
    return build_billboard_response_record(current_asset_record)
