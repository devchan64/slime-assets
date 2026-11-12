"""격리 PostgreSQL에서 시작 동기화·충돌·롤백을 검증한다."""
from copy import deepcopy
import os
import unittest

import psycopg
from psycopg.rows import dict_row
from database.migrate import apply_asset_migrations, read_asset_database_secret
from database.test_migrations import AssetDatabaseMigrationTests
from asset_service.content_asset_store import ContentRevisionConflict
from asset_service.ledger_sync import synchronize_ledger_bundle


class LedgerStartupSyncTests(unittest.TestCase):
    setUpClass = classmethod(AssetDatabaseMigrationTests.setUpClass.__func__)
    tearDownClass = classmethod(AssetDatabaseMigrationTests.tearDownClass.__func__)

    def setUp(current_test_case):
        AssetDatabaseMigrationTests.setUp(current_test_case)
        apply_asset_migrations(current_test_case.current_database_connection)
        current_test_case.current_seed_bundle = {'billboards': {'types': {'vegetation': '자연물'}, 'assets': [{
            'assetId': 'test-grass', 'assetType': 'vegetation', 'name': '검증 풀', 'metadata': {},
            'image': {'imageId': 'image.test-grass', 'version': 'v1', 'path': 'assets/billboards/test-grass.png',
                      'sha256': 'a'*64, 'width': 128, 'height': 128, 'publishedPath': '/assets/test-grass.png'},
            'properties': {'anchorX': 0.48, 'anchorY': 0.79, 'groundFootprintColumns': 1, 'groundFootprintRows': 1,
                           'displayHeight': 128.0, 'billboardBlocksMovement': False, 'billboardAnchorMode': 'tile-intersection',
                           'shadowOffsetColumn': 0.0, 'shadowOffsetRow': 0.0, 'shadowScaleX': 1.0, 'shadowScaleY': 1.0,
                           'shadowCoordinateVersion': 2, 'shadowSizeVersion': 2}}]},
            'maps': {'documents': {'city_layouts/index.yaml': {'managementId': 'map.index', 'data': {'cities': []}}},
                     'buildingProperties': {'test-building': {'anchorX': 0.48, 'anchorY': 0.79, 'displayWidthAdjustment': -93, 'displayHeightAdjustment': 0}}},
            'effects': [{'id': 'test-effect', 'name': '효과', 'image': 'assets/sprites/effects/test.png',
                         'renderer': 'swarm', 'columns': 1, 'rows': 1, 'frames': 1}]}

    def open_runtime_connection(current_test_case):
        current_database_connection = psycopg.connect(host='127.0.0.1', port=int(os.environ['ASSET_TEST_DATABASE_PORT']),
            user='asset_test_owner', dbname='slime_assets_local', password=read_asset_database_secret(os.environ['ASSET_TEST_PASSWORD_FILE']), row_factory=dict_row)
        current_database_connection.execute('SET ROLE asset_content_writer')
        current_database_connection.commit()
        return current_database_connection

    def execute_startup_sync(current_test_case, current_seed_bundle=None):
        with current_test_case.open_runtime_connection() as current_database_connection:
            return synchronize_ledger_bundle(current_database_connection, current_seed_bundle or current_test_case.current_seed_bundle, lambda: None)

    def test_initial_repeat_update_and_revert(current_test_case):
        current_first_result = current_test_case.execute_startup_sync()
        current_test_case.assertEqual(current_first_result['generation'], 1)
        current_test_case.assertEqual(current_test_case.execute_startup_sync()['changes'], [])
        current_modified_bundle = deepcopy(current_test_case.current_seed_bundle)
        current_modified_bundle['billboards']['assets'][0]['properties']['anchorY'] = 0.5
        current_modified_bundle['maps']['documents']['city_layouts/index.yaml']['data']['cities'] = ['new-city']
        current_modified_bundle['effects'][0]['name'] = '수정 효과'
        current_test_case.assertEqual(current_test_case.execute_startup_sync(current_modified_bundle)['generation'], 2)
        current_test_case.assertEqual(current_test_case.execute_startup_sync(current_modified_bundle)['changes'], [])
        current_test_case.assertEqual(current_test_case.execute_startup_sync()['generation'], 3)
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT asset_revision FROM content.billboard_assets').fetchone()[0], 3)

    def test_pending_edit_blocks_even_unchanged_ledger(current_test_case):
        current_test_case.execute_startup_sync()
        current_test_case.current_database_connection.execute('UPDATE content.generations SET current_generation=2')
        with current_test_case.assertRaisesRegex(ContentRevisionConflict, '미반영'):
            current_test_case.execute_startup_sync()
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT count(*) FROM content.change_receipts').fetchone()[0], 1)

    def test_invalid_effect_rolls_back_prior_updates(current_test_case):
        current_test_case.execute_startup_sync()
        current_modified_bundle = deepcopy(current_test_case.current_seed_bundle)
        current_modified_bundle['billboards']['assets'][0]['name'] = '롤백 대상'
        current_modified_bundle['effects'][0]['frames'] = 2
        with current_test_case.assertRaises(ValueError):
            current_test_case.execute_startup_sync(current_modified_bundle)
        current_test_case.assertEqual(current_test_case.execute_startup_sync()['changes'], [])

    def test_immutable_image_conflict_rolls_back(current_test_case):
        current_test_case.execute_startup_sync()
        current_modified_bundle = deepcopy(current_test_case.current_seed_bundle)
        current_modified_bundle['billboards']['assets'][0]['image']['sha256'] = 'b'*64
        with current_test_case.assertRaisesRegex(ContentRevisionConflict, '불변'):
            current_test_case.execute_startup_sync(current_modified_bundle)
        current_test_case.assertEqual(current_test_case.execute_startup_sync()['changes'], [])

    def test_dry_run_and_source_change_roll_back(current_test_case):
        with current_test_case.open_runtime_connection() as current_database_connection:
            synchronize_ledger_bundle(current_database_connection, current_test_case.current_seed_bundle, lambda: None)
            current_database_connection.rollback()
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT current_generation FROM content.generations').fetchone()[0], 0)
        def reject_changed_source():
            raise ValueError('원장 변경')
        with current_test_case.assertRaisesRegex(ValueError, '원장 변경'):
            with current_test_case.open_runtime_connection() as current_database_connection:
                synchronize_ledger_bundle(current_database_connection, current_test_case.current_seed_bundle, reject_changed_source)
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT count(*) FROM content.billboard_assets').fetchone()[0], 0)

    def test_removal_retires_and_readdition_advances_revision(current_test_case):
        current_test_case.execute_startup_sync()
        current_modified_bundle = deepcopy(current_test_case.current_seed_bundle)
        current_modified_bundle['billboards']['assets'] = []
        current_modified_bundle['maps']['documents'] = {}
        current_modified_bundle['maps']['buildingProperties'] = {}
        current_modified_bundle['effects'] = []
        current_test_case.execute_startup_sync(current_modified_bundle)
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT retired FROM content.billboard_assets').fetchone()[0], True)
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT count(*) FROM content.image_versions').fetchone()[0], 1)
        current_test_case.execute_startup_sync()
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT asset_revision FROM content.billboard_assets').fetchone()[0], 3)

    def test_referenced_removal_and_running_export_rejected(current_test_case):
        current_test_case.current_seed_bundle['maps']['documents']['city_layouts/index.yaml']['data']['objects'] = [
            {'id': 'placed-grass', 'kind': 'billboard', 'textureKey': 'test-grass', 'position': {'column': 0, 'row': 0}}]
        current_test_case.execute_startup_sync()
        current_modified_bundle = deepcopy(current_test_case.current_seed_bundle)
        current_modified_bundle['billboards']['assets'] = []
        with current_test_case.assertRaisesRegex(ContentRevisionConflict, '사용 중'):
            current_test_case.execute_startup_sync(current_modified_bundle)
        from asset_service.content_snapshots import begin_content_snapshot_export
        with current_test_case.open_runtime_connection() as current_database_connection:
            begin_content_snapshot_export(current_database_connection, True)
        with current_test_case.assertRaisesRegex(ContentRevisionConflict, '출력 작업'):
            current_test_case.execute_startup_sync()

    def test_building_adjustment_query_export_and_seed(current_test_case):
        from asset_service.content_runtime_documents import read_shared_property_document
        from asset_service.content_snapshots import begin_content_snapshot_export
        current_test_case.execute_startup_sync()
        with current_test_case.open_runtime_connection() as current_database_connection:
            current_property_values = read_shared_property_document(current_database_connection)['data']['test-building']
            current_test_case.assertEqual(current_property_values['displayWidthAdjustment'], -93)
            current_snapshot_values = begin_content_snapshot_export(current_database_connection, True)['payload']
            current_test_case.assertEqual(current_snapshot_values['buildingProperties']['test-building'], current_property_values)
            current_database_connection.rollback()
