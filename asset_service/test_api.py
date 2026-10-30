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
            current_export_response=current_api_client.post('/internal/v1/content/snapshots/export',json={})
            current_test_case.assertEqual(current_export_response.status_code,200,current_export_response.text)
            current_export_payload=current_export_response.json()['payload']
            if os.environ.get('ASSET_TEST_LEDGER_SOURCE_ROOT'):
                import subprocess
                current_workflow_root=Path(os.environ['ASSET_TEST_WORKFLOW_ROOT']).resolve()
                with tempfile.TemporaryDirectory() as current_directory_name:
                    current_export_path=Path(current_directory_name)/'export.json'
                    current_export_path.write_text(json.dumps(current_export_payload))
                    current_environment_values=os.environ.copy()
                    current_environment_values.update(ASSET_LEDGER_EXPORT_FILE=str(current_export_path),
                        ASSET_LEDGER_SOURCE_ROOT=os.environ['ASSET_TEST_LEDGER_SOURCE_ROOT'])
                    current_test_result=subprocess.run([str(current_workflow_root/'.venv-management/bin/python'),'-m','unittest',
                        'tools.review.tests.test_asset_ledger_roundtrip','-v'],cwd=current_workflow_root,
                        env=current_environment_values,capture_output=True,text=True,timeout=30)
                    current_test_case.assertEqual(current_test_result.returncode,0,current_test_result.stdout+current_test_result.stderr)
            current_test_case.assertEqual(current_export_payload['mapDocuments'],current_seed_bundle['maps']['documents'])
            current_test_case.assertEqual(len(current_export_payload['assets']),len(current_seed_bundle['billboards']['assets']))
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/bundle',json=current_seed_bundle).json(),current_seed_response.json())
            current_test_case.assertEqual(current_api_client.post('/internal/v1/content/seed/bundle',content=b'x'*(ASSET_SEED_MAXIMUM_BYTES+1)).status_code,413)

    @unittest.skipUnless(os.environ.get('ASSET_TEST_WORKFLOW_ROOT'), '관리도구 HTTP 검증 저장소를 명시해야 합니다.')
    def test_management_gateway_over_real_http(current_test_case):
        import socket
        import subprocess
        import threading
        import time
        import uvicorn
        current_workflow_root=Path(os.environ['ASSET_TEST_WORKFLOW_ROOT']).resolve()
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
                current_test_result=subprocess.run([str(current_workflow_root/'.venv-management/bin/python'),'-m','unittest',
                    'tools.review.tests.test_asset_service_http','-v'],cwd=current_workflow_root,env=current_environment_values,
                    capture_output=True,text=True,timeout=25)
                current_test_case.assertEqual(current_test_result.returncode,0,current_test_result.stdout+current_test_result.stderr)
        finally:
            current_server_value.should_exit=True
            current_server_thread.join(timeout=10)
            current_server_socket.close()
            current_test_case.assertFalse(current_server_thread.is_alive(),'격리 API 종료 실패')

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
