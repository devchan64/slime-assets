"""에셋 파일 전달 묶음을 최초 DB 시드로 적재한다."""
import hashlib
import json
from typing import Annotated
from urllib.parse import urlsplit, unquote
from pydantic import BaseModel, ConfigDict, Field, model_validator
from psycopg import sql
from psycopg.types.json import Jsonb
from asset_service.content_asset_contracts import BillboardPropertyValues, ContentAssetIdentifier, BILLBOARD_PROPERTY_COLUMN_NAMES


class ContentImageSeed(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    imageId: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: str = Field(pattern=r'^assets/[^\\]+$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    publishedPath: str = Field(min_length=1)

    @model_validator(mode='after')
    def validate_image_relative_path(self):
        if any(current_path_part in ('', '.', '..') for current_path_part in self.path.split('/')):
            raise ValueError('이미지 상대 경로 오류')
        current_public_parts=urlsplit(self.publishedPath)
        current_decoded_path=unquote(current_public_parts.path)
        if (current_public_parts.scheme or current_public_parts.netloc or current_public_parts.query or current_public_parts.fragment
                or not current_decoded_path.startswith('/') or current_decoded_path.startswith('//')
                or '\\' in current_decoded_path or any(ord(current_character)<32 for current_character in self.publishedPath)
                or any(current_path_part in ('','.','..') for current_path_part in current_decoded_path[1:].split('/'))
                or not current_decoded_path.endswith('.png')):
            raise ValueError('이미지 게시 경로는 동일 출처의 안전한 PNG 절대 경로여야 합니다.')
        return self


class BillboardAssetSeed(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    assetId: ContentAssetIdentifier
    assetType: ContentAssetIdentifier
    name: str = Field(min_length=1)
    image: ContentImageSeed
    properties: BillboardPropertyValues
    metadata: dict


class BillboardSeedBundle(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    types: dict[str, str]
    assets: Annotated[list[BillboardAssetSeed], Field(min_length=1)]

    @model_validator(mode='after')
    def validate_seed_unique_references(self):
        current_seen_identifiers = set()
        current_image_versions = {}
        for current_asset_record in self.assets:
            if current_asset_record.assetId in current_seen_identifiers:
                raise ValueError('에셋 ID 중복: ' + current_asset_record.assetId)
            current_seen_identifiers.add(current_asset_record.assetId)
            if current_asset_record.assetType not in self.types:
                raise ValueError('미등록 에셋 분류: ' + current_asset_record.assetType)
            current_image_key = (current_asset_record.image.imageId, current_asset_record.image.version)
            if current_image_key in current_image_versions and current_image_versions[current_image_key] != current_asset_record.image:
                raise ValueError('동일 이미지 버전의 내용 불일치')
            current_image_versions[current_image_key] = current_asset_record.image
        return self


def initialize_billboard_seed_bundle(current_database_connection, current_bundle_values):
    """기존 운영 데이터를 덮어쓰지 않고 최초 묶음을 원자적으로 적재한다."""
    current_seed_bundle = BillboardSeedBundle.model_validate(current_bundle_values)
    current_bundle_hash = hashlib.sha256(json.dumps(current_seed_bundle.model_dump(),
        ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    current_generation_record = current_database_connection.execute(
        'SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    if current_generation_record is None:
        raise ValueError('콘텐츠 스키마 초기화가 필요합니다.')
    current_seed_receipt = current_database_connection.execute(
        'SELECT request_sha256,response_values FROM content.change_receipts WHERE request_id=%s',
        ('billboard-initial-seed',)).fetchone()
    if current_seed_receipt is not None:
        if current_seed_receipt['request_sha256'] != current_bundle_hash:
            raise ValueError('최초 시드가 이미 적재되었습니다. 변경 import 계획이 필요합니다.')
        return current_seed_receipt['response_values']
    if current_database_connection.execute('SELECT 1 FROM content.billboard_assets LIMIT 1').fetchone():
        raise ValueError('기존 에셋 DB에는 최초 시드를 적용할 수 없습니다.')
    for current_type_identifier, current_type_label in current_seed_bundle.types.items():
        current_database_connection.execute('INSERT INTO content.asset_types VALUES(%s,%s)',
            (current_type_identifier, current_type_label))
    current_registered_images = set()
    current_property_columns = ['asset_id', *BILLBOARD_PROPERTY_COLUMN_NAMES.values()]
    current_property_statement = sql.SQL('INSERT INTO content.billboard_properties ({}) VALUES ({})').format(
        sql.SQL(',').join(map(sql.Identifier, current_property_columns)),
        sql.SQL(',').join(sql.Placeholder() for current_column_name in current_property_columns))
    for current_asset_record in current_seed_bundle.assets:
        current_image_record = current_asset_record.image
        current_image_key = (current_image_record.imageId, current_image_record.version)
        if current_image_key not in current_registered_images:
            current_database_connection.execute('''INSERT INTO content.image_versions
                (image_id,image_version,source_path,image_sha256,image_width,image_height,published_path)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''', (*current_image_key, current_image_record.path,
                current_image_record.sha256, current_image_record.width, current_image_record.height, current_image_record.publishedPath))
            current_registered_images.add(current_image_key)
        current_database_connection.execute('''INSERT INTO content.billboard_assets
            (asset_id,asset_type,display_name,image_id,image_version,asset_revision,metadata)
            VALUES(%s,%s,%s,%s,%s,1,%s)''', (current_asset_record.assetId,current_asset_record.assetType,
            current_asset_record.name,*current_image_key,Jsonb(current_asset_record.metadata)))
        current_property_values = current_asset_record.properties.model_dump()
        current_database_connection.execute(current_property_statement, (current_asset_record.assetId,
            *(current_property_values[current_field_name] for current_field_name in BILLBOARD_PROPERTY_COLUMN_NAMES)))
    current_next_generation = current_generation_record['current_generation'] + 1
    current_response_values = {'generation':current_next_generation,'count':len(current_seed_bundle.assets),'sha256':current_bundle_hash}
    current_database_connection.execute('UPDATE content.generations SET current_generation=%s WHERE singleton=true', (current_next_generation,))
    current_database_connection.execute('''INSERT INTO content.change_receipts
        (request_id,request_sha256,content_generation,changed_by,before_values,after_values,response_values)
        VALUES(%s,%s,%s,%s,%s,%s,%s)''', ('billboard-initial-seed',current_bundle_hash,current_next_generation,
        'initial-seed',Jsonb({}),Jsonb(current_response_values),Jsonb(current_response_values)))
    return current_response_values
