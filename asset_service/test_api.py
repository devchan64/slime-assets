"""격리 DB에서 기존 API의 편집·원장 전달 및 인증 경계를 검증한다."""
from contextlib import ExitStack
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient

from database.migrate import apply_asset_migrations, read_asset_database_secret
from database.test_migrations import AssetDatabaseMigrationTests
from asset_service.api import create_asset_service, ASSET_REQUEST_MAXIMUM_BYTES
from asset_service.content_asset_seed import initialize_billboard_seed_bundle
from asset_service.connection import validate_asset_runtime_connection


class AssetServiceApiTests(unittest.TestCase):
    setUpClass = classmethod(AssetDatabaseMigrationTests.setUpClass.__func__)
    tearDownClass = classmethod(AssetDatabaseMigrationTests.tearDownClass.__func__)

    def setUp(current_test_case):
        AssetDatabaseMigrationTests.setUp(current_test_case)
        apply_asset_migrations(current_test_case.current_database_connection)
        current_test_case.current_resource_stack = ExitStack()
        current_test_case.addCleanup(current_test_case.current_resource_stack.close)
        current_directory_name = current_test_case.current_resource_stack.enter_context(tempfile.TemporaryDirectory())
        current_token_path = Path(current_directory_name)/'service-token'
        current_token_path.write_text('isolated-test-service-token')
        current_token_path.chmod(0o600)
        current_test_case.current_resource_stack.enter_context(patch.dict(os.environ,{
            'ASSET_DB_STAGE':'local','ASSET_SERVICE_ALLOWED_HOSTS':'localhost',
            'SLIME_CONTENT_SERVICE_SECRET_FILE':str(current_token_path)}))
        current_test_case.current_property_values = {'anchorX':0.0,'anchorY':0.0,'groundFootprintColumns':1,
            'groundFootprintRows':1,'displayHeight':128.0,'billboardBlocksMovement':False,
            'billboardAnchorMode':'tile-intersection','shadowOffsetColumn':0.0,'shadowOffsetRow':0.0,
            'shadowScaleX':1.0,'shadowScaleY':1.0,'shadowCoordinateVersion':2,'shadowSizeVersion':2}
        with current_test_case.open_runtime_connection() as current_database_connection:
            initialize_billboard_seed_bundle(current_database_connection,{'types':{'vegetation':'자연물'},'assets':[{
                'assetId':'test-grass','assetType':'vegetation','name':'검증 풀','properties':current_test_case.current_property_values,'metadata':{},
                'image':{'imageId':'image.test-grass','version':'v1','path':'assets/billboards/test-grass.png',
                    'sha256':'a'*64,'width':128,'height':128,'publishedPath':'/assets/test-grass.png'}}]})

    def open_runtime_connection(current_test_case):
        current_database_connection = psycopg.connect(host='127.0.0.1',port=int(os.environ['ASSET_TEST_DATABASE_PORT']),
            user='asset_test_owner',dbname='slime_assets_local',password=read_asset_database_secret(os.environ['ASSET_TEST_PASSWORD_FILE']),row_factory=dict_row)
        current_database_connection.execute('SET ROLE asset_content_writer')
        current_database_connection.commit()
        return current_database_connection

    def open_asset_test_client(current_test_case):
        return TestClient(create_asset_service(current_test_case.open_runtime_connection),base_url='http://localhost',
            headers={'Authorization':'Bearer isolated-test-service-token'})

    def test_edit_retry_conflict_and_export_completion(current_test_case):
        with current_test_case.open_asset_test_client() as current_api_client:
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/types').json(),{'types':{'vegetation':'자연물'}})
            current_request_payload={'requestId':'edit-one','expectedRevision':1,'properties':current_test_case.current_property_values | {'anchorY':0.5}}
            current_saved_response=current_api_client.patch('/internal/v1/content/billboards/test-grass',json=current_request_payload)
            current_test_case.assertEqual(current_saved_response.status_code,200,current_saved_response.text)
            current_test_case.assertEqual(current_api_client.patch('/internal/v1/content/billboards/test-grass',json=current_request_payload).json(),current_saved_response.json())
            current_test_case.assertEqual(current_api_client.patch('/internal/v1/content/billboards/test-grass',json=current_request_payload | {'requestId':'other'}).status_code,409)
            current_export_response=current_api_client.post('/internal/v1/content/snapshots/export',json={})
            current_test_case.assertEqual(current_export_response.status_code,200,current_export_response.text)
            current_export_record=current_export_response.json()
            current_test_case.assertEqual(current_export_record['payload']['assets'][0]['properties']['anchorY'],0.5)
            current_test_case.assertTrue(current_api_client.get('/internal/v1/content/status').json()['pending'])
            current_complete_response=current_api_client.post('/internal/v1/content/snapshots/'+current_export_record['runId']+'/complete',json={'payloadHash':current_export_record['payloadHash'],'manifestHash':current_export_record['payloadHash']})
            current_test_case.assertEqual(current_complete_response.status_code,200,current_complete_response.text)
            current_test_case.assertFalse(current_api_client.get('/internal/v1/content/status').json()['pending'])

    def test_auth_host_origin_and_size_rejected(current_test_case):
        with current_test_case.open_asset_test_client() as current_api_client:
            current_route_path='/internal/v1/content/types'
            current_test_case.assertEqual(current_api_client.get(current_route_path,headers={'Authorization':'wrong'}).status_code,401)
            current_test_case.assertEqual(current_api_client.get(current_route_path,headers={'Host':'invalid.example'}).status_code,400)
            current_test_case.assertEqual(current_api_client.get(current_route_path,headers={'Origin':'https://invalid.example'}).status_code,403)
            current_test_case.assertEqual(current_api_client.patch('/internal/v1/content/billboards/test-grass',content=b'x'*(ASSET_REQUEST_MAXIMUM_BYTES+1)).status_code,413)
            current_test_case.assertEqual(current_api_client.patch('/internal/v1/content/billboards/test-grass',json={'unknown':True}).status_code,422)

    def test_production_startup_rejected(current_test_case):
        with patch.dict(os.environ,{'ASSET_DB_STAGE':'prod'}),current_test_case.assertRaisesRegex(ValueError,'local/dev'):
            with current_test_case.open_asset_test_client():
                pass

    def test_unknown_metadata_prevents_export_commit(current_test_case):
        current_test_case.current_database_connection.execute("UPDATE content.billboard_assets SET metadata='{" + '"privateEditor":"internal"' + "}'::jsonb")
        with current_test_case.open_asset_test_client() as current_api_client:
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/snapshots/export',json={}).status_code,422)
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT count(*) FROM content.snapshot_runs').fetchone()[0],0)

    def test_owner_connection_rejected(current_test_case):
        with current_test_case.open_runtime_connection() as current_database_connection:
            current_database_connection.execute('RESET ROLE')
            with current_test_case.assertRaisesRegex(ValueError,'관리자 DB 역할'):
                validate_asset_runtime_connection(current_database_connection)

    def test_monster_spawn_export_rejected(current_test_case):
        current_test_case.current_database_connection.execute("""INSERT INTO content.map_documents
            VALUES('monster_spawns/index.yaml','private-spawns',1,'{}','{}',false)""")
        with current_test_case.open_asset_test_client() as current_api_client:
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/snapshots/export',json={}).status_code,422)
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT count(*) FROM content.snapshot_runs').fetchone()[0],0)

    def test_map_seed_review_edit_retry_and_conflict(current_test_case):
        current_map_document = {'managementId':'map.names.test-city','data':{'name':'검증 도시'}}
        current_seed_bundle = {'documents':{
            'field_tiles/index.yaml':{'managementId':'map.fields.index','data':{}},
            'city_layouts/index.yaml':{'managementId':'map.cities.index','data':{'includes':{'layouts':['test-city']}}},
            'city_layouts/test-city.yaml':{'managementId':'map.city.test-city','data':{'rows':['aa','aa']}},
            'map_names/test-city.yaml':current_map_document},'buildingProperties':{}}
        with current_test_case.open_asset_test_client() as current_api_client:
            current_seed_response = current_api_client.post('/internal/v1/content/seed/maps',json=current_seed_bundle)
            current_test_case.assertEqual(current_seed_response.status_code,200,current_seed_response.text)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/maps',json=current_seed_bundle).json(),current_seed_response.json())
            current_review_response = current_api_client.get('/internal/v1/content/review-bundle',params={'map':'test-city'})
            current_test_case.assertEqual(current_review_response.status_code,200,current_review_response.text)
            current_test_case.assertEqual(current_review_response.json()['documents']['city_layouts/test-city.yaml']['data']['rows'],['aa','aa'])
            current_request_payload = {'requestId':'map-change-one','expectedGeneration':current_seed_response.json()['generation'],
                'documents':[{'path':'map_names/test-city.yaml','revision':1,'document':{'managementId':'map.names.test-city','data':{'name':'변경 도시'}}}]}
            current_saved_response = current_api_client.post('/internal/v1/content/map-changes',json=current_request_payload)
            current_test_case.assertEqual(current_saved_response.status_code,200,current_saved_response.text)
            current_test_case.assertNotIn('validationErrors',current_saved_response.json())
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/map-changes',json=current_request_payload).json(),current_saved_response.json())
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/map-changes',json=current_request_payload | {'requestId':'map-stale'}).status_code,409)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/map-document',params={'path':'map_names/test-city.yaml'}).json()['data']['name'],'변경 도시')
            current_export_response = current_api_client.post('/internal/v1/content/snapshots/export',json={})
            current_test_case.assertEqual(current_export_response.status_code,200,current_export_response.text)
            current_test_case.assertEqual(current_export_response.json()['payload']['mapRevisions']['map_names/test-city.yaml'],2)
            current_invalid_payload = {'requestId':'map-invalid-batch','expectedGeneration':current_saved_response.json()['generation'],
                'documents':[
                    {'path':'map_names/test-city.yaml','revision':2,'document':current_map_document},
                    {'path':'city_layouts/test-city.yaml','revision':1,'document':{'managementId':'map.city.test-city','data':{}}}]}
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/map-changes',json=current_invalid_payload).status_code,422)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/map-document',params={'path':'map_names/test-city.yaml'}).json()['data']['name'],'변경 도시')

    def test_building_lifecycle_generation_receipts_and_usage(current_test_case):
        current_seed_bundle={'documents':{
            'city_layouts/index.yaml':{'managementId':'map.city.index','data':{'includes':{'layouts':['test-city']}}},
            'city_layouts/test-city.yaml':{'managementId':'map.city.test','data':{'buildings':[{'sprite':{'textureKey':'town-used-v1'}}]}}},
            'buildingProperties':{'town-used-v1':{'anchorX':0.5,'anchorY':1}}}
        with current_test_case.open_asset_test_client() as current_api_client:
            current_capability_response=current_api_client.get('/internal/v1/content/building-lifecycle-capabilities')
            current_test_case.assertEqual(current_capability_response.json(),{'register':True,'retire':True})
            current_seed_response=current_api_client.post('/internal/v1/content/seed/maps',json=current_seed_bundle)
            current_test_case.assertEqual(current_seed_response.status_code,200,current_seed_response.text)
            current_generation_value=current_seed_response.json()['generation']
            current_register_path='/internal/v1/content/buildings/town-new-v1/lifecycle/register'
            current_register_payload={'requestId':'building-register-test','expectedGeneration':current_generation_value,'properties':{'anchorX':0.5,'anchorY':1}}
            current_register_response=current_api_client.post(current_register_path,json=current_register_payload)
            current_test_case.assertEqual(current_register_response.status_code,200,current_register_response.text)
            current_test_case.assertEqual(current_api_client.post(current_register_path,json=current_register_payload).json(),current_register_response.json())
            current_test_case.assertEqual(current_api_client.post(current_register_path,json={**current_register_payload,'properties':{'anchorX':1,'anchorY':1}}).status_code,409)
            current_retire_path='/internal/v1/content/buildings/town-new-v1/lifecycle/retire'
            current_retire_payload={'requestId':'building-retire-test','expectedGeneration':current_generation_value}
            current_test_case.assertEqual(current_api_client.post(current_retire_path,json=current_retire_payload).status_code,409)
            current_retire_payload['expectedGeneration']=current_register_response.json()['generation']
            current_used_response=current_api_client.post('/internal/v1/content/buildings/town-used-v1/lifecycle/retire',json={**current_retire_payload,'requestId':'building-used-test'})
            current_test_case.assertEqual(current_used_response.status_code,422,current_used_response.text)
            current_retire_response=current_api_client.post(current_retire_path,json=current_retire_payload)
            current_test_case.assertEqual(current_retire_response.status_code,200,current_retire_response.text)
            current_test_case.assertEqual(current_api_client.post(current_retire_path,json=current_retire_payload).json(),current_retire_response.json())
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/buildings/ward-tower-v1/lifecycle/retire',json={**current_retire_payload,'requestId':'tower-test','expectedGeneration':current_retire_response.json()['generation']}).status_code,422)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/buildings/town-new-v1/properties').status_code,422)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/buildings/town-used-v1/properties').status_code,200)

    def test_private_map_paths_rejected_before_seed(current_test_case):
        with current_test_case.open_asset_test_client() as current_api_client:
            for current_invalid_path in ('monster_spawns/index.yaml','terrain/../private.yaml','terrain//index.yaml','/terrain/index.yaml'):
                current_seed_bundle = {'documents':{current_invalid_path:{'managementId':'private.data','data':{}}},'buildingProperties':{}}
                current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/maps',json=current_seed_bundle).status_code,422)
                current_test_case.assertEqual(current_api_client.get('/internal/v1/content/map-document',params={'path':current_invalid_path}).status_code,422)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/review-bundle',params={'map':'../private'}).status_code,422)
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT count(*) FROM content.map_documents').fetchone()[0],0)

    def test_complete_seed_rollback_retry_and_effect_edit(current_test_case):
        from uuid import uuid4
        from asset_service.content_effect_assets import hash_effect_asset_document
        AssetDatabaseMigrationTests.setUp(current_test_case)
        apply_asset_migrations(current_test_case.current_database_connection)
        current_effect_document = {'id':'test-glow','name':'검증 발광','image':'assets/sprites/effects/test.png',
            'renderer':'billboard','columns':4,'rows':2,'frames':8,
            'alignment':{'texture':'test-grass','key':'test-glow','alpha':0.5,'target':[0,0,1,1],'frames':[[0,0,1,1]]*8}}
        current_seed_bundle = {'billboards':{'types':{'vegetation':'자연물'},'assets':[{
            'assetId':'test-grass','assetType':'vegetation','name':'검증 풀','properties':current_test_case.current_property_values,'metadata':{},
            'image':{'imageId':'image.test-grass','version':'v1','path':'assets/billboards/test-grass.png',
                'sha256':'a'*64,'width':128,'height':128,'publishedPath':'/assets/test-grass.png'}}]},
            'maps':{'documents':{'monster_spawns/index.yaml':{'managementId':'private.spawns','data':{}}},'buildingProperties':{}},
            'effects':[current_effect_document]}
        with current_test_case.open_asset_test_client() as current_api_client:
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/bundle',json=current_seed_bundle).status_code,422)
            for current_table_name in ('billboard_assets','effect_assets','change_receipts','map_documents'):
                from psycopg import sql
                current_test_case.assertEqual(current_test_case.current_database_connection.execute(sql.SQL('SELECT count(*) FROM content.{}').format(sql.Identifier(current_table_name))).fetchone()[0],0)
            current_seed_bundle['maps']['documents']={'map_names/test.yaml':{'managementId':'map.test','data':{'name':'테스트'}}}
            current_seed_response=current_api_client.post('/internal/v1/content/seed/bundle',json=current_seed_bundle)
            current_test_case.assertEqual(current_seed_response.status_code,200,current_seed_response.text)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/bundle',json=current_seed_bundle).json(),current_seed_response.json())
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/bundle',json=current_seed_bundle | {'effects':[]}).status_code,409)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/effects').json()['effects'],[current_effect_document])
            current_edit_payload={'requestId':str(uuid4()),'expectedRevision':hash_effect_asset_document(current_effect_document),
                'alignment':current_effect_document['alignment'] | {'alpha':0.8}}
            current_saved_response=current_api_client.patch('/internal/v1/content/effects/test-glow',json=current_edit_payload)
            current_test_case.assertEqual(current_saved_response.status_code,200,current_saved_response.text)
            current_test_case.assertEqual(current_api_client.patch('/internal/v1/content/effects/test-glow',json=current_edit_payload).json(),current_saved_response.json())
            current_test_case.assertEqual(current_api_client.patch('/internal/v1/content/effects/test-glow',json=current_edit_payload | {'requestId':str(uuid4())}).status_code,409)
            current_test_case.assertEqual(current_api_client.patch('/internal/v1/content/effects/test-glow',json=current_edit_payload | {'requestId':12}).status_code,422)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/bundle',json=current_seed_bundle).json(),current_seed_response.json())
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/effects').json()['effects'][0]['alignment']['alpha'],0.8)

    def test_batch_failure_rolls_back_and_retry_preserves_revision(current_test_case):
        current_change_record={'assetId':'test-grass','requestId':'batch-grass','expectedRevision':1,
            'properties':current_test_case.current_property_values | {'anchorY':0.6}}
        with current_test_case.open_asset_test_client() as current_api_client:
            current_failed_response=current_api_client.post('/internal/v1/content/billboard-changes',json={'changes':[
                current_change_record,current_change_record | {'assetId':'missing-grass','requestId':'batch-missing'}]})
            current_test_case.assertEqual(current_failed_response.status_code,422,current_failed_response.text)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/billboards/test-grass').json()['revision'],1)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/billboard-changes',json={'changes':[current_change_record,current_change_record]}).status_code,422)
            current_saved_response=current_api_client.post('/internal/v1/content/billboard-changes',json={'changes':[current_change_record]})
            current_test_case.assertEqual(current_saved_response.status_code,200,current_saved_response.text)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/billboard-changes',json={'changes':[current_change_record]}).json(),current_saved_response.json())
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/billboards/test-grass').json()['revision'],2)

    def test_image_version_is_immutable_and_previous_image_remains(current_test_case):
        current_image_record={'imageId':'image.test-grass','version':'v2','path':'assets/billboards/test-grass-v2.png',
            'sha256':'b'*64,'width':128,'height':128,'publishedPath':'/assets/test-grass-v2.png'}
        current_save_payload={'requestId':'image-change','expectedRevision':1,
            'properties':current_test_case.current_property_values,'image':current_image_record}
        with current_test_case.open_asset_test_client() as current_api_client:
            current_saved_response=current_api_client.post('/internal/v1/content/billboards/test-grass/image',json=current_save_payload)
            current_test_case.assertEqual(current_saved_response.status_code,200,current_saved_response.text)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/billboards/test-grass/image',json=current_save_payload).json(),current_saved_response.json())
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/billboards/test-grass/image',json=current_save_payload | {
                'requestId':'image-conflict','expectedRevision':2,'image':current_image_record | {'sha256':'c'*64}}).status_code,409)
            for current_image_digest in ('a'*64,'b'*64):
                current_test_case.assertEqual(current_api_client.get('/internal/v1/content/images/'+current_image_digest).status_code,200)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/images/invalid').status_code,422)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/billboards/test-grass').json()['revision'],2)

    def test_retirement_removes_placements_but_preserves_image(current_test_case):
        current_map_document={'managementId':'map.city.test','data':{'rows':['aa'], 'objects':[
            {'id':'grass-one','kind':'billboard','position':{'column':0,'row':0},'textureKey':'test-grass'}]}}
        with current_test_case.open_asset_test_client() as current_api_client:
            current_seed_response=current_api_client.post('/internal/v1/content/seed/maps',json={
                'documents':{'city_layouts/test.yaml':current_map_document},'buildingProperties':{}})
            current_test_case.assertEqual(current_seed_response.status_code,200,current_seed_response.text)
            current_preview_response=current_api_client.post('/internal/v1/content/retirement-preview',json={'assetId':'test-grass','expectedRevision':1})
            current_test_case.assertEqual(current_preview_response.status_code,200,current_preview_response.text)
            current_test_case.assertEqual(current_preview_response.json()['maps'][0]['removed'],1)
            current_retirement_payload={'assetId':'test-grass','expectedRevision':1,
                'expectedGeneration':current_preview_response.json()['expectedGeneration'],'requestId':'retire-grass'}
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/retirement',json=current_retirement_payload | {'expectedGeneration':0}).status_code,409)
            current_saved_response=current_api_client.post('/internal/v1/content/retirement',json=current_retirement_payload)
            current_test_case.assertEqual(current_saved_response.status_code,200,current_saved_response.text)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/retirement',json=current_retirement_payload).json(),current_saved_response.json())
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/map-document',params={'path':'city_layouts/test.yaml'}).json()['data']['objects'],[])
            current_test_case.assertTrue(current_api_client.get('/internal/v1/content/billboards/test-grass').json()['retired'])
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/images/'+'a'*64).status_code,200)
            current_export_response=current_api_client.post('/internal/v1/content/snapshots/export',json={})
            current_test_case.assertEqual(current_export_response.json()['payload']['mapRevisions']['city_layouts/test.yaml'],2)

    @unittest.skipUnless(os.environ.get('ASSET_TEST_SEED_FILE'), '명시적으로 전달한 현재 원장 fixture가 필요합니다.')
    def test_current_ledger_seed_export_roundtrip(current_test_case):
        import json
        from asset_service.api import ASSET_SEED_MAXIMUM_BYTES
        AssetDatabaseMigrationTests.setUp(current_test_case)
        apply_asset_migrations(current_test_case.current_database_connection)
        current_seed_bundle=json.loads(Path(os.environ['ASSET_TEST_SEED_FILE']).read_text())
        with current_test_case.open_asset_test_client() as current_api_client:
            current_seed_response=current_api_client.post('/internal/v1/content/seed/bundle',json=current_seed_bundle)
            current_test_case.assertEqual(current_seed_response.status_code,200,current_seed_response.text)
            from uuid import uuid4
            from asset_service.content_effect_assets import hash_effect_asset_document
            current_waypoint_source=next(current_effect_record for current_effect_record in current_seed_bundle['effects'] if current_effect_record.get('renderer')=='waypoint-animation')
            current_waypoint_original={**current_waypoint_source,'id':'isolated-ledger-waypoint-old'}
            current_waypoint_replacement={**current_waypoint_source,'id':'isolated-ledger-waypoint-new'}
            current_register_response=current_api_client.post('/internal/v1/content/effects',json=current_waypoint_original)
            current_test_case.assertEqual(current_register_response.status_code,200,current_register_response.text)
            current_replace_response=current_api_client.post('/internal/v1/content/effects/isolated-ledger-waypoint-old/replace',json={'requestId':str(uuid4()),'expectedRevision':hash_effect_asset_document(current_waypoint_original),'effect':current_waypoint_replacement})
            current_test_case.assertEqual(current_replace_response.status_code,200,current_replace_response.text)
            current_export_response=current_api_client.post('/internal/v1/content/snapshots/export',json={})
            current_test_case.assertEqual(current_export_response.status_code,200,current_export_response.text)
            current_export_payload=current_export_response.json()['payload']
            current_test_case.assertIn(current_waypoint_replacement,current_export_payload['effects'])
            current_test_case.assertNotIn(current_waypoint_original,current_export_payload['effects'])
            if os.environ.get('ASSET_TEST_LEDGER_SOURCE_ROOT'):
                import subprocess
                current_workflow_root=Path(os.environ['ASSET_TEST_WORKFLOW_ROOT']).resolve()
                with tempfile.TemporaryDirectory() as current_directory_name:
                    current_export_path=Path(current_directory_name)/'export.json'
                    current_export_path.write_text(json.dumps(current_export_payload))
                    current_environment_values=os.environ.copy()
                    current_environment_values.update(ASSET_LEDGER_EXPORT_FILE=str(current_export_path),
                        ASSET_LEDGER_SOURCE_ROOT=os.environ['ASSET_TEST_LEDGER_SOURCE_ROOT'])
                    current_workflow_python=os.environ.get('ASSET_TEST_WORKFLOW_PYTHON',str(current_workflow_root/'.venv/bin/python'))
                    current_test_result=subprocess.run([current_workflow_python,'-m','unittest',
                        'tools.review.tests.test_asset_ledger_roundtrip','-v'],cwd=current_workflow_root,
                        env=current_environment_values,capture_output=True,text=True,timeout=30)
                    current_test_case.assertEqual(current_test_result.returncode,0,current_test_result.stdout+current_test_result.stderr)
            current_test_case.assertEqual(current_export_payload['mapDocuments'],current_seed_bundle['maps']['documents'])
            current_test_case.assertEqual(len(current_export_payload['assets']),len(current_seed_bundle['billboards']['assets']))
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/bundle',json=current_seed_bundle).json(),current_seed_response.json())
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/bundle',content=b'x'*(ASSET_SEED_MAXIMUM_BYTES+1)).status_code,413)

    @unittest.skipUnless(os.environ.get('ASSET_TEST_WORKFLOW_ROOT'), '관리도구 HTTP 검증 저장소를 명시해야 합니다.')
    def test_building_management_recovers_lost_responses_over_http(current_test_case):
        import base64
        import io
        import socket
        import sys
        import threading
        import time
        import uvicorn
        import yaml
        from PIL import Image
        from asset_service.content_map_seed import initialize_map_seed_bundle
        current_workflow_root=Path(os.environ['ASSET_TEST_WORKFLOW_ROOT']).resolve()
        sys.path.insert(0,str(current_workflow_root))
        current_test_case.addCleanup(sys.path.remove,str(current_workflow_root))
        from tools.review.domains.map_assets import building_editor
        from tools.review.domains.hermes_agent import building_operations
        from tools.review.domains.hermes_agent.delegation_store import HermesDelegationStore
        from tools.review.domains.hermes_agent.service import HermesServiceManager
        from tools.review.domains.map_assets import resize
        from tools.review.common.management_gateway import ManagementCommandGateway, execute_management_command
        from tools.review.gateway_server import create_gateway_request_handler
        from http.server import ThreadingHTTPServer
        with current_test_case.open_runtime_connection() as current_database_connection:
            current_seed_result=initialize_map_seed_bundle(current_database_connection,{'documents':{
                'city_layouts/index.yaml':{'managementId':'map.city.index','data':{'includes':{'layouts':{}}}},
                'field_tiles/index.yaml':{'managementId':'map.field.index','data':{'includes':{'maps':{}}}}},'buildingProperties':{}})
        current_server_socket=socket.socket()
        current_server_socket.bind(('127.0.0.1',0))
        current_service_port=current_server_socket.getsockname()[1]
        with patch.dict(os.environ,{'ASSET_SERVICE_ALLOWED_HOSTS':'localhost,127.0.0.1'}):
            current_http_application=create_asset_service(current_test_case.open_runtime_connection)
        current_server_value=uvicorn.Server(uvicorn.Config(current_http_application,log_level='error',ws='none'))
        current_server_thread=threading.Thread(target=current_server_value.run,kwargs={'sockets':[current_server_socket]},daemon=True)
        current_server_thread.start()
        try:
            current_start_deadline=time.monotonic()+10
            while not current_server_value.started:
                if not current_server_thread.is_alive() or time.monotonic()>current_start_deadline:raise RuntimeError('격리 HTTP API 시작 실패')
                time.sleep(0.05)
            with tempfile.TemporaryDirectory() as current_directory_name:
                current_asset_root=Path(current_directory_name)
                (current_asset_root/'assets/buildings').mkdir(parents=True)
                current_registry_path=current_asset_root/'asset-registry.yaml'
                current_registry_path.write_text('assets: []\n')
                current_image_value=Image.new('RGBA',(8,8),(0,0,0,0))
                current_image_value.putpixel((4,4),(1,2,3,255))
                current_image_stream=io.BytesIO()
                current_image_value.save(current_image_stream,format='PNG')
                current_register_payload={'texture':'town-http-test-v1','generation':current_seed_result['generation'],'requestId':'building-http-register','imageBase64':base64.b64encode(current_image_stream.getvalue()).decode(),'properties':{'anchorX':0.5,'anchorY':1}}
                current_original_request=building_editor.execute_content_api_request
                current_lost_responses={'register':True,'retire':True}
                def execute_and_lose_response(current_http_method,current_route_path,current_request_payload=None):
                    current_response_record=current_original_request(current_http_method,current_route_path,current_request_payload)
                    for current_action_name in ('register','retire'):
                        if current_http_method=='POST' and current_route_path.endswith('/lifecycle/'+current_action_name) and current_lost_responses[current_action_name]:
                            current_lost_responses[current_action_name]=False
                            raise OSError('응답 유실 모의')
                    return current_response_record
                with patch.dict(os.environ,{'SLIME_CONTENT_SERVICE_ORIGIN':'http://127.0.0.1:'+str(current_service_port)}),patch.object(building_editor,'load_registered_tiles',return_value=(current_asset_root,{})),patch.object(building_editor,'execute_content_api_request',side_effect=execute_and_lose_response):
                    with current_test_case.assertRaisesRegex(RuntimeError,'DB 등록을 확인하지 못했습니다'):
                        building_editor.execute_building_lifecycle(current_register_payload,'register')
                    current_register_result=building_editor.execute_building_lifecycle(current_register_payload,'register')
                    current_test_case.assertTrue(current_register_result['saved'])
                    current_image_path=current_asset_root/'assets/buildings/town-http-test-v1.png'
                    current_registry_records={'assets/buildings/town-http-test-v1.png':{'width':8,'height':8}}
                    current_hermes_manager=HermesServiceManager(current_asset_root/'hermes-jobs',current_asset_root/'hermes-sessions')
                    current_gateway_service=ManagementCommandGateway({'hermes-agent':current_hermes_manager.handle_hermes_request,'map-assets':resize.handle_map_resize_request})
                    current_gateway_server=ThreadingHTTPServer(('127.0.0.1',0),create_gateway_request_handler(current_gateway_service.handle))
                    current_gateway_server.emit_gateway_trace=lambda *current_values:None
                    current_gateway_thread=threading.Thread(target=current_gateway_server.serve_forever,daemon=True)
                    current_gateway_thread.start()
                    current_gateway_origin='http://127.0.0.1:'+str(current_gateway_server.server_port)
                    try:
                        with patch.dict(os.environ,{'SLIME_MANAGEMENT_GATEWAY_URL':current_gateway_origin,'SLIME_CONTENT_STORAGE':'database'}),patch.object(resize,'RESIZE_STORAGE_DIRECTORY',current_asset_root/'resize-locks'),patch.object(building_editor,'load_registered_tiles',return_value=(current_asset_root,current_registry_records)),patch.object(building_editor,'resolve_registered_asset',return_value=(current_image_path,{})),patch.object(building_operations,'HermesDelegationStore',return_value=HermesDelegationStore(current_asset_root/'receipts')):
                            current_catalog_record=execute_management_command('hermes-agent','hermes-building-catalog',{'query':'town-http-test-v1'},current_gateway_origin)
                            current_properties_value=current_catalog_record['items'][0]['display_properties']
                            current_draft_record={'texture':'town-http-test-v1','generation':current_catalog_record['generation'],'expected':current_properties_value,'properties':{**current_properties_value,'anchorX':0.6}}
                            current_review_record=execute_management_command('hermes-agent','hermes-building-preview',{'draft':current_draft_record},current_gateway_origin)
                            current_save_payload={'request_id':'building_anchor_http','draft':current_draft_record,'review_sha256':current_review_record['review_sha256']}
                            current_save_record=execute_management_command('hermes-agent','hermes-building-save',current_save_payload,current_gateway_origin)
                            current_test_case.assertEqual(current_save_record['state'],'completed')
                            current_test_case.assertTrue(execute_management_command('hermes-agent','hermes-building-save',current_save_payload,current_gateway_origin)['duplicate'])
                            with current_test_case.assertRaisesRegex(ValueError,'세대'):
                                execute_management_command('hermes-agent','hermes-building-preview',{'draft':current_draft_record},current_gateway_origin)
                            current_updated_catalog=execute_management_command('hermes-agent','hermes-building-catalog',{'query':'town-http-test-v1'},current_gateway_origin)
                            current_test_case.assertEqual(current_updated_catalog['items'][0]['display_properties']['anchorX'],0.6)
                    finally:
                        current_gateway_server.shutdown()
                        current_gateway_thread.join(timeout=5)
                        current_gateway_server.server_close()
                        current_test_case.assertFalse(current_gateway_thread.is_alive(),'격리 게이트웨이 종료 실패')
                    current_retire_payload={'texture':'town-http-test-v1','generation':current_updated_catalog['generation'],'requestId':'building-http-retire'}
                    with current_test_case.assertRaisesRegex(OSError,'응답 유실'):
                        building_editor.execute_building_lifecycle(current_retire_payload,'retire')
                    current_test_case.assertNotIn('lifecycle_status',yaml.safe_load(current_registry_path.read_text())['assets'][0]['source'])
                    current_retire_result=building_editor.execute_building_lifecycle(current_retire_payload,'retire')
                    current_test_case.assertTrue(current_retire_result['saved'])
                    current_test_case.assertEqual(yaml.safe_load(current_registry_path.read_text())['assets'][0]['source']['lifecycle_status'],'retired')
                    current_test_case.assertTrue((current_asset_root/'assets/buildings/town-http-test-v1.png').is_file())
                with current_test_case.open_runtime_connection() as current_database_connection:
                    current_test_case.assertIsNone(current_database_connection.execute("SELECT 1 FROM content.building_properties WHERE asset_id='town-http-test-v1'").fetchone())
                    current_test_case.assertEqual(current_database_connection.execute("SELECT count(*) AS total FROM content.change_receipts WHERE request_id IN ('building-http-register','building-http-retire')").fetchone()['total'],2)
        finally:
            current_server_value.should_exit=True
            current_server_thread.join(timeout=10)
            current_server_socket.close()
            current_test_case.assertFalse(current_server_thread.is_alive(),'격리 API 종료 실패')

    @unittest.skipUnless(os.environ.get('ASSET_TEST_WORKFLOW_ROOT'), '관리도구 HTTP 검증 저장소를 명시해야 합니다.')
    def test_management_gateway_over_real_http(current_test_case):
        import socket
        import subprocess
        import threading
        import time
        import uvicorn
        current_workflow_root=Path(os.environ['ASSET_TEST_WORKFLOW_ROOT']).resolve()
        current_test_case.current_database_connection.execute("UPDATE content.billboard_assets SET metadata='{\"modeling\":{}}'::jsonb WHERE asset_id='test-grass'")
        from asset_service.content_effect_assets import seed_effect_asset_records
        import yaml
        current_catalog_path=Path(__file__).resolve().parents[1]/'assets/sprites/effects/catalog.yaml'
        current_catalog_record=yaml.safe_load(current_catalog_path.read_text(encoding='utf-8'))
        current_effect_document=next(current_record for current_record in current_catalog_record['effects'] if current_record['id']=='firefly-swarm-v1')
        with current_test_case.open_runtime_connection() as current_database_connection:
            seed_effect_asset_records(current_database_connection,[current_effect_document])
        from asset_service.content_map_seed import initialize_map_seed_bundle
        current_fixture_documents={
            'field_tiles/index.yaml':{'managementId':'map.field-index','data':{'includes':{'tiles':{'test-field':'test-field.yaml'}}}},
            'city_layouts/index.yaml':{'managementId':'map.city-index','data':{'includes':{'layouts':{}}}},
            'field_tiles/codes.yaml':{'managementId':'map.field-codes','data':{'g':'grass'}},
            'map_names/test-field.yaml':{'managementId':'map.name-test','data':{'name':'테스트 필드'}},
            'field_tiles/test-field.yaml':{'managementId':'map.field-test','data':{'rows':['ggggg']*5,'objects':[
                {'id':'grass-one','kind':'decoration','textureKey':'test-grass','position':{'column':1,'row':1}},
                {'id':'grass-two','kind':'decoration','textureKey':'test-grass','position':{'column':2,'row':1}}]}},
            'terrain/maps/test-field.yaml':{'managementId':'map.terrain-test','data':{'columns':5,'rows':5,'elevations':[[0]*5 for current_row_index in range(5)]}},
            'map_spawns/test-field.yaml':{'managementId':'map.spawns-test','data':{}},
            'field_waypoints/test-field.yaml':{'managementId':'map.waypoints-test','data':{}}}
        with current_test_case.open_runtime_connection() as current_database_connection:
            initialize_map_seed_bundle(current_database_connection,{'documents':current_fixture_documents,'buildingProperties':{}})
        current_server_socket=socket.socket()
        current_server_socket.bind(('127.0.0.1',0))
        current_service_port=current_server_socket.getsockname()[1]
        with patch.dict(os.environ, {'ASSET_SERVICE_ALLOWED_HOSTS':'localhost,127.0.0.1'}):
            current_http_application=create_asset_service(current_test_case.open_runtime_connection)
        current_server_value=uvicorn.Server(uvicorn.Config(current_http_application,log_level='error',ws='none'))
        current_server_thread=threading.Thread(target=current_server_value.run,kwargs={'sockets':[current_server_socket]},daemon=True)
        current_server_thread.start()
        try:
            current_start_deadline=time.monotonic()+10
            while not current_server_value.started:
                if not current_server_thread.is_alive() or time.monotonic()>current_start_deadline:
                    raise RuntimeError('격리 HTTP API 시작 실패')
                time.sleep(0.05)
            with tempfile.TemporaryDirectory() as current_directory_name:
                current_wrong_token_path=Path(current_directory_name)/'wrong-token'
                current_wrong_token_path.write_text('isolated-wrong-token')
                current_wrong_token_path.chmod(0o600)
                current_environment_values=os.environ.copy()
                current_environment_values.update(ASSET_HTTP_ISOLATED_TEST='1',
                    ASSET_HTTP_WRONG_TOKEN_FILE=str(current_wrong_token_path),
                    SLIME_CONTENT_SERVICE_ORIGIN='http://127.0.0.1:'+str(current_service_port))
                current_test_result=subprocess.run([os.environ.get('ASSET_TEST_WORKFLOW_PYTHON',str(current_workflow_root/'.venv/bin/python')),'-m','unittest',
                    'tools.review.tests.test_asset_service_http','-v'],cwd=current_workflow_root,env=current_environment_values,
                    capture_output=True,text=True,timeout=25)
                current_test_case.assertEqual(current_test_result.returncode,0,current_test_result.stdout+current_test_result.stderr)
        finally:
            current_server_value.should_exit=True
            current_server_thread.join(timeout=10)
            current_server_socket.close()
            current_test_case.assertFalse(current_server_thread.is_alive(),'격리 API 종료 실패')

    @unittest.skipUnless(os.environ.get('ASSET_TEST_WORKFLOW_ROOT') and os.environ.get('ASSET_TEST_LEGACY_BACKEND_ROOT'), '관리도구와 기존 백엔드 경로를 명시해야 합니다.')
    def test_legacy_backend_waypoint_management_over_real_http(current_test_case):
        import socket
        import subprocess
        import sys
        import threading
        import time
        import uvicorn
        current_backend_root=Path(os.environ['ASSET_TEST_LEGACY_BACKEND_ROOT']).resolve()
        current_workflow_root=Path(os.environ['ASSET_TEST_WORKFLOW_ROOT']).resolve()
        with current_test_case.open_runtime_connection() as current_database_connection:
            current_database_connection.execute("UPDATE content.billboard_assets SET metadata='{\"modeling\":{}}'::jsonb WHERE asset_id='test-grass'")
        sys.path.insert(0,str(current_backend_root))
        current_test_case.addCleanup(sys.path.remove,str(current_backend_root))
        from app import content_internal_api
        current_server_socket=socket.socket()
        current_server_socket.bind(('127.0.0.1',0))
        current_service_port=current_server_socket.getsockname()[1]
        current_server_value=uvicorn.Server(uvicorn.Config(content_internal_api.CONTENT_INTERNAL_API_APP,log_level='error',ws='none'))
        current_server_thread=threading.Thread(target=current_server_value.run,kwargs={'sockets':[current_server_socket]},daemon=True)
        try:
            with patch.object(content_internal_api,'connect',current_test_case.open_runtime_connection),patch.object(content_internal_api,'read_internal_service_secret',return_value='isolated-test-service-token'):
                current_server_thread.start()
                current_start_deadline=time.monotonic()+10
                while not current_server_value.started:
                    if not current_server_thread.is_alive() or time.monotonic()>current_start_deadline:
                        raise RuntimeError('격리 기존 콘텐츠 API 시작 실패')
                    time.sleep(0.05)
                current_environment_values=os.environ.copy()
                current_environment_values.update(ASSET_HTTP_LEGACY_TEST='1',SLIME_CONTENT_SERVICE_ORIGIN='http://127.0.0.1:'+str(current_service_port))
                current_workflow_python=os.environ.get('ASSET_TEST_WORKFLOW_PYTHON',str(current_workflow_root/'.venv/bin/python'))
                current_test_result=subprocess.run([current_workflow_python,'-m','unittest','tools.review.tests.test_asset_service_http.LegacyAssetServiceHttpTests','-v'],cwd=current_workflow_root,env=current_environment_values,capture_output=True,text=True,timeout=25)
                current_test_case.assertEqual(current_test_result.returncode,0,current_test_result.stdout+current_test_result.stderr)
                from concurrent.futures import ThreadPoolExecutor, TimeoutError
                from threading import Event
                from urllib.error import HTTPError
                from urllib.request import Request, urlopen
                import json
                current_glow_record={'id':'legacy-concurrent-glow','name':'동시 변경 검증','image':'assets/sprites/effects/test.png','renderer':'billboard-glow','columns':4,'rows':2,'frames':8,'alignment':{'texture':'test-grass','key':'legacy-concurrent-glow','alpha':0.5,'target':[0,0,1,1],'frames':[[0,0,1,1]]*8}}
                current_started_event=Event()
                def post_legacy_reviewed_effect(current_revision_value):
                    current_request_value=Request('http://127.0.0.1:'+str(current_service_port)+'/internal/v1/content/effects/reviewed',data=json.dumps({'effect':current_glow_record,'expectedBillboardRevision':current_revision_value}).encode(),headers={'Authorization':'Bearer isolated-test-service-token','Content-Type':'application/json'},method='POST')
                    current_started_event.set()
                    try:
                        with urlopen(current_request_value,timeout=5) as current_response_value:
                            return current_response_value.status
                    except HTTPError as current_http_error:
                        try:
                            return current_http_error.code
                        finally:
                            current_http_error.close()
                current_test_case.assertEqual(post_legacy_reviewed_effect(True),422)
                current_started_event.clear()
                with ThreadPoolExecutor(max_workers=1) as current_executor_pool:
                    with current_test_case.open_runtime_connection() as current_database_connection:
                        current_database_connection.execute('SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE')
                        current_response_future=current_executor_pool.submit(post_legacy_reviewed_effect,1)
                        current_test_case.assertTrue(current_started_event.wait(3))
                        with current_test_case.assertRaises(TimeoutError):
                            current_response_future.result(timeout=0.2)
                        current_database_connection.execute("UPDATE content.billboard_assets SET asset_revision=asset_revision+1 WHERE asset_id='test-grass'")
                        current_database_connection.execute('UPDATE content.generations SET current_generation=current_generation+1 WHERE singleton=true')
                    current_test_case.assertEqual(current_response_future.result(timeout=5),409)
                with current_test_case.open_runtime_connection() as current_database_connection:
                    current_test_case.assertIsNone(current_database_connection.execute("SELECT asset_id FROM content.effect_assets WHERE asset_id='legacy-concurrent-glow'").fetchone())
                    current_database_connection.execute("UPDATE content.billboard_assets SET retired=true WHERE asset_id='test-grass'")
                current_test_case.assertEqual(post_legacy_reviewed_effect(2),409)
        finally:
            current_server_value.should_exit=True
            if current_server_thread.ident is not None:
                current_server_thread.join(timeout=10)
            current_server_socket.close()
            current_test_case.assertFalse(current_server_thread.is_alive(),'격리 기존 API 종료 실패')

    def test_effect_registration_and_waypoint_replacement_are_transactional(current_test_case):
        from uuid import uuid4
        from asset_service.content_effect_assets import hash_effect_asset_document
        current_effect_record={'id':'waypoint-test-old','name':'기존','image':'assets/sprites/effects/waypoint/old.png','animation':'assets/sprites/effects/waypoint/old.json','renderer':'waypoint-animation','columns':4,'rows':2,'frames':8}
        current_replacement_record={**current_effect_record,'id':'waypoint-test-new','name':'새 이펙트'}
        with current_test_case.open_asset_test_client() as current_api_client:
            current_registration_response=current_api_client.post('/internal/v1/content/effects',json=current_effect_record)
            current_test_case.assertEqual(current_registration_response.status_code,200,current_registration_response.text)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects',json=current_effect_record).status_code,422)
            current_first_export=current_api_client.post('/internal/v1/content/snapshots/export',json={}).json()
            current_test_case.assertEqual(current_first_export['payload']['effects'],[current_effect_record])
            current_request_payload={'requestId':str(uuid4()),'expectedRevision':hash_effect_asset_document(current_effect_record),'effect':current_replacement_record}
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects/waypoint-test-old/replace',json={**current_request_payload,'requestId':12}).status_code,422)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects/waypoint-test-old/replace',json={**current_request_payload,'effect':{**current_replacement_record,'alignment':{'texture':'test-texture','key':'test-key','alpha':0.5,'target':[0,0,1,1],'frames':[[0,0,1,1]]*8}}}).status_code,422)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects',json={**current_replacement_record,'renderer':'fog'}).status_code,422)
            current_replacement_response=current_api_client.post('/internal/v1/content/effects/waypoint-test-old/replace',json=current_request_payload)
            current_test_case.assertEqual(current_replacement_response.status_code,200,current_replacement_response.text)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects/waypoint-test-old/replace',json=current_request_payload).json(),current_replacement_response.json())
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects/waypoint-test-old/replace',json={**current_request_payload,'effect':{**current_replacement_record,'name':'다른 입력'}}).status_code,409)
            current_result_values=current_api_client.get('/internal/v1/content/effects').json()['effects']
            current_test_case.assertEqual(current_result_values,[current_replacement_record])
            current_complete_response=current_api_client.post('/internal/v1/content/snapshots/'+current_first_export['runId']+'/complete',json={'payloadHash':current_first_export['payloadHash'],'manifestHash':current_first_export['payloadHash']})
            current_test_case.assertEqual(current_complete_response.status_code,200,current_complete_response.text)
            current_test_case.assertTrue(current_api_client.get('/internal/v1/content/status').json()['pending'])
            current_second_export=current_api_client.post('/internal/v1/content/snapshots/export',json={}).json()
            current_test_case.assertEqual(current_second_export['payload']['effects'],[current_replacement_record])
            current_test_case.assertEqual(current_second_export['payload']['generation'],current_replacement_response.json()['generation'])
            current_complete_response=current_api_client.post('/internal/v1/content/snapshots/'+current_second_export['runId']+'/complete',json={'payloadHash':current_second_export['payloadHash'],'manifestHash':current_second_export['payloadHash']})
            current_test_case.assertEqual(current_complete_response.status_code,200,current_complete_response.text)
            current_test_case.assertFalse(current_api_client.get('/internal/v1/content/status').json()['pending'])

    def test_initial_map_seed_preserves_edited_migration_building(current_test_case):
        current_test_case.current_database_connection.execute("UPDATE content.building_properties SET anchor_y=0.7 WHERE asset_id='ward-tower-v1'")
        current_seed_bundle={'documents':{'city_layouts/index.yaml':{'managementId':'map.index','data':{'cities':[]}}},'buildingProperties':{'ward-tower-v1':{'anchorX':0.5,'anchorY':0.8}}}
        with current_test_case.open_asset_test_client() as current_api_client:
            current_seed_response=current_api_client.post('/internal/v1/content/seed/maps',json=current_seed_bundle)
            current_test_case.assertEqual(current_seed_response.status_code,422,current_seed_response.text)
        current_tower_value=current_test_case.current_database_connection.execute("SELECT anchor_y FROM content.building_properties WHERE asset_id='ward-tower-v1'").fetchone()[0]
        current_test_case.assertEqual(float(current_tower_value),0.7)
        current_test_case.assertEqual(current_test_case.current_database_connection.execute('SELECT count(*) FROM content.map_documents').fetchone()[0],0)

    def test_reviewed_effect_registration_checks_billboard_revision_in_transaction(current_test_case):
        current_alignment_values={'texture':'test-grass','key':'test-glow','alpha':0.5,'target':[0,0,1,1],'frames':[[0,0,1,1]]*8}
        current_effect_record={'id':'test-billboard-glow','name':'발광','image':'assets/sprites/effects/test.png','renderer':'billboard-glow','columns':4,'rows':2,'frames':8,'alignment':current_alignment_values}
        with current_test_case.open_asset_test_client() as current_api_client:
            current_saved_response=current_api_client.patch('/internal/v1/content/billboards/test-grass',json={'requestId':'effect-target-edit','expectedRevision':1,'properties':current_test_case.current_property_values | {'anchorY':0.5}})
            current_test_case.assertEqual(current_saved_response.status_code,200,current_saved_response.text)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects/reviewed',json={'effect':current_effect_record,'expectedBillboardRevision':1}).status_code,409)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/effects').json()['effects'],[])
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects/reviewed',json={'effect':current_effect_record,'expectedBillboardRevision':True}).status_code,422)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects/reviewed',json={'effect':{**current_effect_record,'renderer':'fog'},'expectedBillboardRevision':2}).status_code,422)
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects/reviewed',json={'effect':{**current_effect_record,'alignment':{**current_alignment_values,'texture':'missing-billboard'}},'expectedBillboardRevision':2}).status_code,409)
            current_saved_response=current_api_client.post('/internal/v1/content/effects/reviewed',json={'effect':current_effect_record,'expectedBillboardRevision':2})
            current_test_case.assertEqual(current_saved_response.status_code,200,current_saved_response.text)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/effects').json()['effects'],[current_effect_record])

    def test_reviewed_effect_registration_rejects_retired_target(current_test_case):
        current_effect_record={'id':'retired-glow','name':'발광','image':'assets/sprites/effects/test.png','renderer':'billboard-glow','columns':4,'rows':2,'frames':8,'alignment':{'texture':'test-grass','key':'retired-glow','alpha':0.5,'target':[0,0,1,1],'frames':[[0,0,1,1]]*8}}
        current_test_case.current_database_connection.execute("UPDATE content.billboard_assets SET retired=true WHERE asset_id='test-grass'")
        with current_test_case.open_asset_test_client() as current_api_client:
            current_response_value=current_api_client.post('/internal/v1/content/effects/reviewed',json={'effect':current_effect_record,'expectedBillboardRevision':1})
            current_test_case.assertEqual(current_response_value.status_code,409,current_response_value.text)
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/effects').json()['effects'],[])

    def test_reviewed_effect_registration_waits_for_concurrent_billboard_change(current_test_case):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        from threading import Event
        current_alignment_values={'texture':'test-grass','key':'concurrent-glow','alpha':0.5,'target':[0,0,1,1],'frames':[[0,0,1,1]]*8}
        current_effect_record={'id':'concurrent-glow','name':'발광','image':'assets/sprites/effects/test.png','renderer':'billboard-glow','columns':4,'rows':2,'frames':8,'alignment':current_alignment_values}
        current_started_event=Event()
        def submit_reviewed_registration():
            with current_test_case.open_asset_test_client() as current_api_client:
                current_started_event.set()
                return current_api_client.post('/internal/v1/content/effects/reviewed',json={'effect':current_effect_record,'expectedBillboardRevision':1}).status_code
        with ThreadPoolExecutor(max_workers=1) as current_executor_pool:
            with current_test_case.open_runtime_connection() as current_database_connection:
                current_database_connection.execute('SELECT current_generation FROM content.generations WHERE singleton=true FOR UPDATE')
                current_response_future=current_executor_pool.submit(submit_reviewed_registration)
                current_test_case.assertTrue(current_started_event.wait(3))
                with current_test_case.assertRaises(TimeoutError):
                    current_response_future.result(timeout=0.2)
                current_database_connection.execute("UPDATE content.billboard_assets SET asset_revision=asset_revision+1 WHERE asset_id='test-grass'")
                current_database_connection.execute('UPDATE content.generations SET current_generation=current_generation+1 WHERE singleton=true')
            current_test_case.assertEqual(current_response_future.result(timeout=5),409)
        with current_test_case.open_asset_test_client() as current_api_client:
            current_test_case.assertEqual(current_api_client.get('/internal/v1/content/effects').json()['effects'],[])

    def test_effect_registration_rejects_concurrent_alignment_collisions(current_test_case):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        current_alignment_values={'texture':'test-texture','key':'test-key','alpha':0.5,'target':[0,0,1,1],'frames':[[0,0,1,1] for current_frame_index in range(8)]}
        current_effect_record={'id':'test-glow-one','name':'발광','image':'assets/sprites/effects/test.png','renderer':'fog','columns':4,'rows':2,'frames':8,'alignment':current_alignment_values}
        current_start_barrier=Barrier(2)
        def submit_effect_registration(current_submitted_record):
            with current_test_case.open_asset_test_client() as current_api_client:
                current_start_barrier.wait(timeout=10)
                return current_api_client.post('/internal/v1/content/effects',json=current_submitted_record).status_code
        with ThreadPoolExecutor(max_workers=2) as current_executor_pool:
            current_status_values=list(current_executor_pool.map(submit_effect_registration,[current_effect_record,{**current_effect_record,'id':'test-glow-two','alignment':{**current_alignment_values,'key':'other-key'}}]))
        current_test_case.assertEqual(sorted(current_status_values),[200,422])
        with current_test_case.open_asset_test_client() as current_api_client:
            current_key_collision={**current_effect_record,'id':'test-glow-three','alignment':{**current_alignment_values,'texture':'other-texture','key':'other-key' if current_status_values[1]==200 else 'test-key'}}
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/effects',json=current_key_collision).status_code,422)
            current_test_case.assertEqual(len(current_api_client.get('/internal/v1/content/effects').json()['effects']),1)

    def test_completed_ledger_can_be_explicitly_replayed(current_test_case):
        with current_test_case.open_asset_test_client() as current_api_client:
            current_export_record=current_api_client.post('/internal/v1/content/snapshots/export',json={}).json()
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/snapshots/'+current_export_record['runId']+'/complete',json={
                'payloadHash':current_export_record['payloadHash'],'manifestHash':current_export_record['payloadHash']}).status_code,200)
            current_test_case.assertFalse(current_api_client.post('/internal/v1/content/snapshots/export',json={}).json()['needed'])
            current_replay_record=current_api_client.post('/internal/v1/content/snapshots/export?replay=true',json={}).json()
            current_test_case.assertTrue(current_replay_record['needed'])
            current_test_case.assertEqual(current_replay_record['payload'],current_export_record['payload'])
            current_test_case.assertNotEqual(current_replay_record['runId'],current_export_record['runId'])
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/snapshots/export?replay=true',json={}).status_code,409)

    def test_local_loopback_access_without_token(current_test_case):
        with patch.dict(os.environ, {'ASSET_SERVICE_AUTH_MODE':'local-loopback'}):
            with TestClient(create_asset_service(current_test_case.open_runtime_connection),base_url='http://localhost',client=('127.0.0.1',50000)) as current_api_client:
                current_test_case.assertEqual(current_api_client.get('/internal/v1/content/types').status_code,200)
                current_test_case.assertEqual(current_api_client.get('/internal/v1/content/types',headers={'Origin':'https://other.example'}).status_code,403)
            with TestClient(create_asset_service(current_test_case.open_runtime_connection),base_url='http://localhost',client=('192.0.2.1',50000)) as current_api_client:
                current_test_case.assertEqual(current_api_client.get('/internal/v1/content/types').status_code,403)
        with patch.dict(os.environ, {'ASSET_SERVICE_AUTH_MODE':'local-loopback','ASSET_DB_STAGE':'dev'}):
            with current_test_case.assertRaisesRegex(ValueError,'local 개발'):
                with current_test_case.open_asset_test_client():
                    pass
