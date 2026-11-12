"""현재 Git 작업 트리의 원장을 독립 에셋 서비스 입력으로 변환한다."""
import hashlib
import json
import subprocess
from pathlib import Path

import yaml
from asset_service.content_asset_seed import BillboardSeedBundle
from asset_service.content_map_documents import split_map_document_records


class LedgerUniqueLoader(yaml.SafeLoader):
    """중복 키를 허용하지 않는 원장 로더."""


def construct_ledger_mapping(current_yaml_loader, current_yaml_node):
    current_result_mapping = {}
    for current_key_node, current_value_node in current_yaml_node.value:
        current_key_value = current_yaml_loader.construct_object(current_key_node)
        if not isinstance(current_key_value, str) or current_key_value in current_result_mapping:
            raise ValueError('원장 YAML 키는 중복 없는 문자열이어야 합니다.')
        current_result_mapping[current_key_value] = current_yaml_loader.construct_object(current_value_node)
    return current_result_mapping


LedgerUniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_ledger_mapping)


class AssetLedgerSource:
    """추적 파일만 읽고 적용 직전에 입력 변경을 다시 확인한다."""

    def __init__(current_ledger_source, current_repository_root):
        current_ledger_source.current_repository_root = Path(current_repository_root).resolve()
        current_tracked_output = subprocess.run(
            ['git', '-c', 'core.fsmonitor=false', 'ls-files', '-z'],
            cwd=current_ledger_source.current_repository_root, check=True, capture_output=True).stdout
        current_ledger_source.current_tracked_paths = set(current_tracked_output.decode().split('\0'))
        current_ledger_source.current_file_hashes = {}

    def read_ledger_bytes(current_ledger_source, current_relative_path):
        current_source_path = current_ledger_source.current_repository_root / current_relative_path
        if (current_relative_path not in current_ledger_source.current_tracked_paths
                or not current_source_path.resolve().is_relative_to(current_ledger_source.current_repository_root)
                or any(current_path_part.is_symlink() for current_path_part in (current_source_path, *current_source_path.parents))):
            raise ValueError('원장은 Git 추적된 일반 파일이어야 합니다: ' + current_relative_path)
        current_source_bytes = current_source_path.read_bytes()
        current_ledger_source.current_file_hashes[current_relative_path] = hashlib.sha256(current_source_bytes).hexdigest()
        return current_source_bytes

    def read_ledger_document(current_ledger_source, current_relative_path):
        current_source_record = yaml.load(current_ledger_source.read_ledger_bytes(current_relative_path), Loader=LedgerUniqueLoader)
        if not isinstance(current_source_record, dict):
            raise ValueError('원장 문서는 객체여야 합니다: ' + current_relative_path)
        json.dumps(current_source_record, allow_nan=False)
        return current_source_record

    def verify_ledger_unchanged(current_ledger_source):
        current_reloaded_source = AssetLedgerSource(current_ledger_source.current_repository_root)
        current_reloaded_source.build_ledger_bundle()
        if current_reloaded_source.current_file_hashes != current_ledger_source.current_file_hashes:
            raise ValueError('동기화 중 원장 파일·추적 상태가 변경되었습니다. 다시 실행하세요.')

    def build_ledger_bundle(current_ledger_source):
        current_catalog_index = current_ledger_source.read_ledger_document('assets/billboards/catalog.yaml')
        if set(current_catalog_index) != {'schema_version', 'description', 'scope', 'reference_contract', 'placement_guidance', 'review_categories', 'review_category_guidance', 'modeling_contract', 'catalog_files'}:
            raise ValueError('빌보드 색인 필수 필드 누락 또는 알 수 없는 필드')
        if current_catalog_index.get('schema_version') != 2:
            raise ValueError('빌보드 원장 schema_version=2가 필요합니다.')
        current_category_records = current_catalog_index['review_categories']
        if set(current_catalog_index['catalog_files']) != set(current_category_records):
            raise ValueError('빌보드 타입별 원장 색인 불일치')
        current_property_document = current_ledger_source.read_ledger_document('assets/maps/sprite-display-properties.yaml')
        if set(current_property_document) != {'managementId', 'data'}:
            raise ValueError('공용 속성 원장 필드 오류')
        current_property_records = current_property_document['data']
        current_registry_document = current_ledger_source.read_ledger_document('asset-registry.yaml')
        if set(current_registry_document) != {'schema_version', 'assets'} or current_registry_document['schema_version'] != 1 or not isinstance(current_registry_document['assets'], list):
            raise ValueError('에셋 등록부 필드·버전·자료형 오류')
        current_registry_records = {}
        for current_registry_record in current_registry_document['assets']:
            current_required_fields = {'managementId', 'version', 'path', 'sha256', 'source'}
            if not isinstance(current_registry_record, dict) or not current_required_fields <= current_registry_record.keys() or set(current_registry_record) - current_required_fields - {'frontend_path', 'name', 'width', 'height'}:
                raise ValueError('에셋 등록 항목 필수 필드 누락 또는 알 수 없는 필드')
            current_registry_key = (current_registry_record['managementId'], current_registry_record['version'])
            if current_registry_key in current_registry_records:
                raise ValueError('등록부 이미지 ID·버전 중복')
            current_registry_records[current_registry_key] = current_registry_record
        current_asset_records = []
        current_family_names = set()
        for current_category_name, current_partition_path in current_catalog_index['catalog_files'].items():
            if current_partition_path != f'catalogs/{current_category_name}.yaml':
                raise ValueError('빌보드 카탈로그 경로 오류')
            current_partition_record = current_ledger_source.read_ledger_document('assets/billboards/' + current_partition_path)
            if (set(current_partition_record) != {'schema_version', 'category', 'families', 'billboards'}
                    or current_partition_record['schema_version'] != 1 or current_partition_record['category'] != current_category_name):
                raise ValueError('빌보드 카탈로그 필드·버전 오류')
            for current_family_name, current_family_record in current_partition_record['families'].items():
                if current_family_name in current_family_names or current_family_record['review_category'] != current_category_name:
                    raise ValueError('빌보드 계열 중복·타입 오류')
                current_family_names.add(current_family_name)
            for current_catalog_asset in current_partition_record['billboards']:
                current_texture_identifier = current_catalog_asset['texture_key']
                current_family_record = current_partition_record['families'][current_catalog_asset['family']]
                current_image_reference = current_catalog_asset['asset_ref']
                current_source_record = current_catalog_asset['source_file']
                current_registry_record = current_registry_records[(current_image_reference['management_id'], current_image_reference['version'])]
                if any(current_source_record[current_field_name] != current_registry_record[current_field_name]
                       for current_field_name in ('path', 'sha256', 'width', 'height')):
                    raise ValueError('카탈로그·등록부 이미지 불일치: ' + current_texture_identifier)
                current_ledger_source.read_ledger_bytes(current_source_record['path'])
                if current_ledger_source.current_file_hashes[current_source_record['path']] != current_source_record['sha256']:
                    raise ValueError('이미지 SHA-256 불일치: ' + current_texture_identifier)
                current_asset_records.append({
                    'assetId': current_texture_identifier, 'assetType': current_category_name, 'name': current_family_record['name'],
                    'image': {'imageId': current_image_reference['management_id'], 'version': current_image_reference['version'],
                              **{current_field_name: current_source_record[current_field_name] for current_field_name in ('path', 'sha256', 'width', 'height')},
                              'publishedPath': '/registered-assets/' + current_source_record['path']},
                    'properties': current_property_records[current_texture_identifier],
                    'metadata': {'family': current_catalog_asset['family'], 'familyDefinition': current_family_record,
                                 'modeling': current_catalog_asset['modeling'], 'mapReferences': current_catalog_asset['map_references'],
                                 'metadataReferences': current_catalog_asset.get('metadata_references', [])}})
        current_billboard_bundle = BillboardSeedBundle.model_validate({'types': current_category_records, 'assets': current_asset_records}).model_dump()
        current_billboard_identifiers = {current_asset_record['assetId'] for current_asset_record in current_asset_records}
        current_map_documents = {}
        current_map_paths = {current_ledger_source.current_repository_root / current_relative_path for current_relative_path in current_ledger_source.current_tracked_paths if current_relative_path.startswith('assets/maps/') and current_relative_path.endswith('.yaml')}
        current_map_paths.update((current_ledger_source.current_repository_root / 'assets/maps').rglob('*.yaml'))
        for current_document_path in sorted(current_map_paths):
            if current_document_path.name == 'sprite-display-properties.yaml':
                continue
            current_relative_path = current_document_path.relative_to(current_ledger_source.current_repository_root).as_posix()
            current_document_record = current_ledger_source.read_ledger_document(current_relative_path)
            current_document_name = current_relative_path.removeprefix('assets/maps/')
            split_map_document_records(current_document_name, current_document_record)
            current_map_documents[current_document_name] = current_document_record
        current_effect_document = current_ledger_source.read_ledger_document('assets/sprites/effects/catalog.yaml')
        if set(current_effect_document) != {'schema_version', 'category', 'name', 'effects'} or current_effect_document['schema_version'] != 1 or current_effect_document['category'] != 'effects':
            raise ValueError('이펙트 카탈로그 형식 오류')
        return {'billboards': current_billboard_bundle, 'maps': {'documents': current_map_documents,
            'buildingProperties': {current_asset_identifier: current_property_values for current_asset_identifier, current_property_values in current_property_records.items()
                                   if current_asset_identifier not in current_billboard_identifiers}}, 'effects': current_effect_document['effects']}
