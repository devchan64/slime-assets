"""기존 /internal/v1/content 계약의 개발 에셋 서비스. 미전환 명령은 등록하지 않는다."""
from contextlib import asynccontextmanager
import hmac
import os
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from database.migrate import read_asset_database_secret
from asset_service.connection import open_asset_service_connection, validate_asset_runtime_connection
from asset_service.content_asset_contracts import BillboardSaveRequest
from asset_service.content_asset_queries import list_billboard_asset_records, read_billboard_asset_record, read_content_generation_state
from asset_service.content_asset_store import ContentRevisionConflict, save_billboard_property_values
from asset_service.content_snapshots import begin_content_snapshot_export, complete_content_snapshot_export, fail_content_snapshot_export, read_content_snapshot_health

from asset_service.content_asset_seed import ContentImageSeed, BillboardSeedBundle, initialize_billboard_seed_bundle
from asset_service.content_map_seed import initialize_map_seed_bundle
from asset_service.content_map_store import save_content_map_documents
from asset_service.content_runtime_documents import read_runtime_map_document, read_review_document_bundle, read_billboard_image_manifest, collect_document_texture_keys

from asset_service.content_seed_bundle import initialize_complete_seed_bundle
from asset_service.content_effect_assets import save_effect_alignment_values

from asset_service.content_asset_retirement import preview_content_asset_retirement, retire_content_asset_record

ASSET_REQUEST_MAXIMUM_BYTES = 1024 * 1024
ASSET_SEED_MAXIMUM_BYTES = 16 * 1024 * 1024
ASSET_SEED_REQUEST_ROUTES = frozenset((
    "/internal/v1/content/seed/bundle", "/internal/v1/content/seed/billboards", "/internal/v1/content/seed/maps"))


class AssetBillboardBatchEntry(BillboardSaveRequest):
    assetId: str = Field(pattern=r'^[a-z][a-z0-9-]*$')


class AssetBillboardBatchRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    changes: list[AssetBillboardBatchEntry] = Field(min_length=1, max_length=1000)


class AssetBillboardImageRequest(BillboardSaveRequest):
    image: ContentImageSeed


class AssetRetirementPreviewRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    assetId: str = Field(pattern=r'^[a-z][a-z0-9-]*$')
    expectedRevision: int = Field(ge=1)


class AssetRetirementRequest(AssetRetirementPreviewRequest):
    requestId: str = Field(min_length=1, max_length=200)
    expectedGeneration: int = Field(ge=0)


class AssetCompletionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    payloadHash: str = Field(pattern=r'^[0-9a-f]{64}$')
    manifestHash: str = Field(pattern=r'^[0-9a-f]{64}$')


class AssetFailureRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    message: str = Field(min_length=1, max_length=2000)


class AssetRequestSizeLimit:
    def __init__(current_middleware_instance, app):
        current_middleware_instance.current_service_application = app

    async def __call__(current_middleware_instance, scope, receive, send):
        if scope['type'] != 'http':
            return await current_middleware_instance.current_service_application(scope, receive, send)
        current_request_limit = ASSET_SEED_MAXIMUM_BYTES if scope["path"] in ASSET_SEED_REQUEST_ROUTES else ASSET_REQUEST_MAXIMUM_BYTES
        current_received_messages = []
        current_received_length = 0
        while True:
            current_request_message = await receive()
            if current_request_message['type'] == 'http.disconnect':
                return
            current_received_length += len(current_request_message.get('body', b''))
            if current_received_length > current_request_limit:
                return await JSONResponse(status_code=413, content={'detail':'요청 크기가 제한을 초과했습니다.'})(scope, receive, send)
            current_received_messages.append(current_request_message)
            if not current_request_message.get('more_body', False):
                break
        async def receive_buffered_request():
            if current_received_messages:
                return current_received_messages.pop(0)
            return await receive()
        await current_middleware_instance.current_service_application(scope, receive_buffered_request, send)


def create_asset_service(current_connection_factory=None):
    current_connection_factory = current_connection_factory or open_asset_service_connection

    @asynccontextmanager
    async def manage_asset_service_lifetime(current_service_application):
        if os.environ.get('ASSET_DB_STAGE', 'local') not in ('local','dev'):
            raise ValueError('에셋 편집 서비스는 local/dev에서만 실행할 수 있습니다.')
        current_auth_mode = os.environ.get('ASSET_SERVICE_AUTH_MODE', 'token')
        if current_auth_mode not in ('token', 'local-loopback'):
            raise ValueError('지원하지 않는 에셋 인증 모드입니다.')
        if current_auth_mode == 'local-loopback' and os.environ.get('ASSET_DB_STAGE', 'local') != 'local':
            raise ValueError('토큰 없는 실행은 local 개발 환경만 허용합니다.')
        current_service_application.state.current_auth_mode = current_auth_mode
        if current_auth_mode == 'token':
            current_secret_path = os.environ.get('SLIME_CONTENT_SERVICE_SECRET_FILE')
            if not current_secret_path:
                raise ValueError('SLIME_CONTENT_SERVICE_SECRET_FILE이 필요합니다.')
            current_service_application.state.current_service_secret = read_asset_database_secret(current_secret_path)
        with current_connection_factory() as current_database_connection:
            validate_asset_runtime_connection(current_database_connection)
            read_content_generation_state(current_database_connection)
        yield

    def authenticate_asset_request(current_http_request: Request,
            current_authorization_value: Annotated[str,Header(alias='Authorization')]=''):
        if current_http_request.app.state.current_auth_mode == 'local-loopback':
            if current_http_request.client is None or current_http_request.client.host not in ('127.0.0.1', '::1'):
                raise HTTPException(status_code=403, detail='로컬 개발 서버의 루프백 접속만 허용합니다.')
        else:
            current_expected_value = 'Bearer ' + current_http_request.app.state.current_service_secret
            if not hmac.compare_digest(current_authorization_value.encode(), current_expected_value.encode()):
                raise HTTPException(status_code=401, detail='에셋 관리 서비스 인증이 필요합니다.')
        current_origin_value = current_http_request.headers.get('origin')
        if current_origin_value:
            current_origin_parts = urlsplit(current_origin_value)
            if (current_origin_parts.scheme not in ('http','https') or current_origin_parts.netloc != current_http_request.headers.get('host')
                    or current_origin_parts.username or current_origin_parts.password or current_origin_parts.path not in ('','/')
                    or current_origin_parts.query or current_origin_parts.fragment):
                raise HTTPException(status_code=403, detail='허용되지 않은 Origin입니다.')

    current_service_application = FastAPI(title='SLIME asset management', docs_url=None, redoc_url=None,
        openapi_url=None, lifespan=manage_asset_service_lifetime, dependencies=[Depends(authenticate_asset_request)])
    current_allowed_hosts = os.environ.get('ASSET_SERVICE_ALLOWED_HOSTS','localhost,127.0.0.1').split(',')
    if any(not current_host_value or '*' in current_host_value or current_host_value.strip()!=current_host_value for current_host_value in current_allowed_hosts):
        raise ValueError('ASSET_SERVICE_ALLOWED_HOSTS에는 명시적인 호스트 이름만 허용합니다.')
    current_service_application.add_middleware(AssetRequestSizeLimit)
    current_service_application.add_middleware(TrustedHostMiddleware, allowed_hosts=current_allowed_hosts)

    @current_service_application.exception_handler(ContentRevisionConflict)
    async def handle_asset_revision_conflict(current_http_request: Request, current_conflict_error: ContentRevisionConflict):
        return JSONResponse(status_code=409, content={'detail':str(current_conflict_error)})

    @current_service_application.exception_handler(ValueError)
    async def handle_asset_validation_failure(current_http_request: Request, current_validation_error: ValueError):
        return JSONResponse(status_code=422, content={'detail':str(current_validation_error)})

    @current_service_application.get('/internal/v1/content/status')
    def read_asset_service_status():
        with current_connection_factory() as current_database_connection:
            current_database_connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return read_content_snapshot_health(current_database_connection)

    @current_service_application.get('/internal/v1/content/types')
    def read_asset_type_catalog():
        with current_connection_factory() as current_database_connection:
            return {'types':{current_type_record['asset_type']:current_type_record['display_name'] for current_type_record in current_database_connection.execute('SELECT * FROM content.asset_types ORDER BY asset_type').fetchall()}}

    @current_service_application.get('/internal/v1/content/billboards')
    def read_asset_billboard_page(current_asset_type: Annotated[str,Query(alias='type')],
            current_page_cursor: Annotated[str,Query(alias='cursor')]='',
            current_page_limit: Annotated[int,Query(alias='limit',ge=1,le=500)]=100):
        with current_connection_factory() as current_database_connection:
            current_database_connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return list_billboard_asset_records(current_database_connection,current_asset_type,current_page_cursor,current_page_limit)

    @current_service_application.get('/internal/v1/content/billboards/{current_asset_identifier}')
    def read_asset_billboard_detail(current_asset_identifier: str):
        with current_connection_factory() as current_database_connection:
            return read_billboard_asset_record(current_database_connection,current_asset_identifier)

    @current_service_application.patch('/internal/v1/content/billboards/{current_asset_identifier}')
    def save_asset_billboard_detail(current_asset_identifier: str, current_save_request: BillboardSaveRequest):
        with current_connection_factory() as current_database_connection:
            return save_billboard_property_values(current_database_connection,current_asset_identifier,current_save_request.model_dump(),'management-gateway')

    @current_service_application.post('/internal/v1/content/snapshots/export')
    def export_asset_ledger_payload(current_replay_requested: Annotated[bool, Query(alias="replay")]=False):
        with current_connection_factory() as current_database_connection:
            current_database_connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            return begin_content_snapshot_export(current_database_connection, current_replay_requested)

    @current_service_application.post('/internal/v1/content/snapshots/{current_run_identifier}/complete')
    def complete_asset_ledger_export(current_run_identifier: UUID, current_request_record: AssetCompletionRequest):
        with current_connection_factory() as current_database_connection:
            return complete_content_snapshot_export(current_database_connection,str(current_run_identifier),current_request_record.payloadHash,current_request_record.manifestHash)

    @current_service_application.post('/internal/v1/content/snapshots/{current_run_identifier}/fail')
    def fail_asset_ledger_export(current_run_identifier: UUID, current_request_record: AssetFailureRequest):
        with current_connection_factory() as current_database_connection:
            return fail_content_snapshot_export(current_database_connection,str(current_run_identifier),current_request_record.message)

    @current_service_application.post('/internal/v1/content/seed/billboards')
    def seed_asset_billboard_records(current_seed_bundle: BillboardSeedBundle):
        with current_connection_factory() as current_database_connection:
            return initialize_billboard_seed_bundle(current_database_connection, current_seed_bundle.model_dump())

    @current_service_application.post('/internal/v1/content/seed/maps')
    def seed_asset_map_documents(current_seed_bundle: dict):
        with current_connection_factory() as current_database_connection:
            return initialize_map_seed_bundle(current_database_connection, current_seed_bundle)

    @current_service_application.get('/internal/v1/content/map-document')
    def read_asset_map_document(current_document_path: Annotated[str, Query(alias='path')]):
        with current_connection_factory() as current_database_connection:
            current_database_connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return read_runtime_map_document(current_database_connection, current_document_path)

    @current_service_application.get('/internal/v1/content/review-bundle')
    def read_asset_review_bundle(current_map_identifier: Annotated[str | None, Query(alias='map')]=None):
        with current_connection_factory() as current_database_connection:
            current_database_connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            current_review_documents = read_review_document_bundle(current_database_connection, current_map_identifier)
            return {
                'documents': current_review_documents,
                'imageManifest': read_billboard_image_manifest(current_database_connection, collect_document_texture_keys(current_review_documents)),
                'revisions': {current_revision_record['document_path']: current_revision_record['document_revision'] for current_revision_record in current_database_connection.execute('SELECT document_path,document_revision FROM content.map_documents').fetchall()},
                'effectProfiles': {current_effect_record['document']['alignment']['texture']: current_effect_record['document']['alignment'] for current_effect_record in current_database_connection.execute('SELECT document FROM content.effect_assets ORDER BY asset_id').fetchall() if 'alignment' in current_effect_record['document']},
                **read_content_generation_state(current_database_connection)}

    @current_service_application.post('/internal/v1/content/map-changes')
    def save_asset_map_documents(current_request_payload: dict):
        with current_connection_factory() as current_database_connection:
            return save_content_map_documents(current_database_connection, current_request_payload)

    @current_service_application.post('/internal/v1/content/seed/bundle')
    def seed_complete_asset_bundle(current_seed_bundle: dict):
        with current_connection_factory() as current_database_connection:
            return initialize_complete_seed_bundle(current_database_connection, current_seed_bundle)

    @current_service_application.get('/internal/v1/content/effects')
    def list_asset_effect_records():
        with current_connection_factory() as current_database_connection:
            return {'effects':[current_effect_record['document'] for current_effect_record in current_database_connection.execute('SELECT document FROM content.effect_assets ORDER BY asset_id').fetchall()]}

    @current_service_application.patch('/internal/v1/content/effects/{current_asset_identifier}')
    def save_asset_effect_alignment(current_asset_identifier: str, current_request_payload: dict):
        with current_connection_factory() as current_database_connection:
            return save_effect_alignment_values(current_database_connection, current_asset_identifier, current_request_payload)

    @current_service_application.post('/internal/v1/content/billboard-changes')
    def save_asset_billboard_batch(current_batch_request: AssetBillboardBatchRequest):
        current_asset_identifiers = [current_batch_entry.assetId for current_batch_entry in current_batch_request.changes]
        current_request_identifiers = [current_batch_entry.requestId for current_batch_entry in current_batch_request.changes]
        if len(set(current_asset_identifiers)) != len(current_asset_identifiers) or len(set(current_request_identifiers)) != len(current_request_identifiers):
            raise ValueError('에셋 ID 또는 요청 ID가 중복되었습니다.')
        with current_connection_factory() as current_database_connection:
            current_saved_records = [save_billboard_property_values(current_database_connection, current_batch_entry.assetId,
                current_batch_entry.model_dump(exclude={'assetId'}), 'management-gateway') for current_batch_entry in current_batch_request.changes]
            return {'saved':True, 'count':len(current_saved_records), 'changes':current_saved_records, 'snapshot':'pending'}

    @current_service_application.post('/internal/v1/content/billboards/{current_asset_identifier}/image')
    def save_asset_billboard_image(current_asset_identifier: str, current_image_request: AssetBillboardImageRequest):
        with current_connection_factory() as current_database_connection:
            return save_billboard_property_values(current_database_connection, current_asset_identifier,
                current_image_request.model_dump(exclude={'image'}), 'management-gateway', current_image_request.image.model_dump())

    @current_service_application.get('/internal/v1/content/images/{current_image_digest}')
    def read_asset_image_version(current_image_digest: Annotated[str, Path(pattern=r'^[0-9a-f]{64}$')]):
        with current_connection_factory() as current_database_connection:
            current_image_record = current_database_connection.execute(
                'SELECT source_path,image_sha256,image_width,image_height FROM content.image_versions WHERE image_sha256=%s ORDER BY source_path LIMIT 1',
                (current_image_digest,)).fetchone()
            if current_image_record is None:
                raise ValueError('등록되지 않은 이미지 해시')
            return {'path':current_image_record['source_path'], 'sha256':current_image_record['image_sha256'],
                'width':current_image_record['image_width'], 'height':current_image_record['image_height']}

    @current_service_application.post('/internal/v1/content/retirement-preview')
    def preview_asset_retirement_request(current_preview_request: AssetRetirementPreviewRequest):
        with current_connection_factory() as current_database_connection:
            current_database_connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return preview_content_asset_retirement(current_database_connection, current_preview_request.assetId, current_preview_request.expectedRevision)

    @current_service_application.post('/internal/v1/content/retirement')
    def execute_asset_retirement_request(current_retirement_request: AssetRetirementRequest):
        with current_connection_factory() as current_database_connection:
            return retire_content_asset_record(current_database_connection, current_retirement_request.model_dump())

    return current_service_application


ASSET_INTERNAL_API_APP = create_asset_service()
