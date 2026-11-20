"""맵 접수 읽기 API는 저장 없이 일치 여부만 제공한다."""
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import httpx
from asset_service.api import create_asset_service


class MapReceiptReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_receipt_api_checks_auth_hash_and_never_writes(current_test_case):
        current_query_records=[]
        current_receipt_record={'request_sha256':'a'*64}

        class TestReceiptConnection:
            def execute(current_connection_instance,current_query_text,current_query_parameters):
                current_query_records.append((current_query_text,current_query_parameters))
                current_test_case.assertTrue(current_query_text.startswith('SELECT request_sha256'))
                return current_connection_instance

            def fetchone(current_connection_instance):
                return current_receipt_record if current_query_records[-1][1]==('existing',) else None

        @contextmanager
        def open_test_receipt_connection():
            yield TestReceiptConnection()

        with patch.dict(os.environ,{'ASSET_SERVICE_ALLOWED_HOSTS':'testserver'}):
            current_application=create_asset_service(open_test_receipt_connection)
            current_application.state.current_auth_mode='token'
            current_application.state.current_service_secret='test-secret-token'
            current_transport=httpx.ASGITransport(app=current_application)
            async with httpx.AsyncClient(transport=current_transport,base_url='http://testserver') as current_test_client:
                current_route_path='/internal/v1/content/map-change-receipt'
                current_request_values={'requestId':'existing','requestSha256':'a'*64}
                current_test_case.assertEqual((await current_test_client.get(current_route_path,params=current_request_values)).status_code,401)
                current_headers_value={'Authorization':'Bearer test-secret-token'}
                current_test_case.assertEqual((await current_test_client.get(current_route_path,params=current_request_values,headers=current_headers_value)).json(),{'state':'matched'})
                current_test_case.assertEqual((await current_test_client.get(current_route_path,params={**current_request_values,'requestSha256':'b'*64},headers=current_headers_value)).json(),{'state':'mismatch'})
                current_test_case.assertEqual((await current_test_client.get(current_route_path,params={**current_request_values,'requestId':'missing'},headers=current_headers_value)).json(),{'state':'not_found'})
                current_test_case.assertEqual((await current_test_client.get(current_route_path,params={**current_request_values,'requestSha256':'invalid'},headers=current_headers_value)).status_code,422)
        current_test_case.assertEqual(len(current_query_records),3)


if __name__=='__main__':
    unittest.main()
