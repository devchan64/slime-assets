"""DB 스냅샷 세대와 외부 파일 게시 완료를 추적한다."""
import hashlib
import json
from uuid import uuid4
from asset_service.content_map_documents import validate_public_map_path
from asset_service.content_asset_queries import BILLBOARD_QUERY_SELECT, build_billboard_response_record
from asset_service.content_asset_store import ContentRevisionConflict

CONTENT_SNAPSHOT_LEASE_SECONDS = 120
PUBLIC_ASSET_METADATA_FIELDS = frozenset(('family','familyDefinition','modeling','mapReferences','metadataReferences'))


def validate_asset_export_boundary(current_snapshot_payload):
    """공개 원장에 전달할 경로와 메타데이터 최상위 경계를 확인한다."""
    for current_asset_record in current_snapshot_payload['assets']:
        if set(current_asset_record['metadata']) - PUBLIC_ASSET_METADATA_FIELDS:
            raise ValueError('공개 에셋 메타데이터에 허용하지 않은 필드가 있습니다.')
    for current_document_name in current_snapshot_payload['mapDocuments']:
        validate_public_map_path(current_document_name)


def hash_content_snapshot_payload(current_snapshot_payload):
    return hashlib.sha256(json.dumps(current_snapshot_payload,ensure_ascii=False,sort_keys=True,
        separators=(',', ':'),allow_nan=False).encode()).hexdigest()


def begin_content_snapshot_export(current_database_connection, current_replay_requested=False):
    """호출자는 repeatable-read 트랜잭션을 사용한다."""
    current_generation_record=current_database_connection.execute(
        'SELECT * FROM content.generations WHERE singleton=true FOR UPDATE').fetchone()
    if current_generation_record is None:
        raise ValueError('콘텐츠 DB 초기화가 필요합니다.')
    current_database_connection.execute("""UPDATE content.snapshot_runs SET state='failed',
        error_message='실행 lease 만료; 다음 스냅샷으로 복구',finished_at=now()
        WHERE state='running' AND lease_expires_at<=now()""")
    if current_database_connection.execute("SELECT 1 FROM content.snapshot_runs WHERE state='running' AND lease_expires_at>now()").fetchone():
        raise ContentRevisionConflict('파일 스냅샷이 이미 실행 중입니다.')
    if not current_replay_requested and current_generation_record['current_generation']==current_generation_record['snapshot_generation']:
        return {'needed':False,'generation':current_generation_record['current_generation']}
    current_type_records=current_database_connection.execute('SELECT * FROM content.asset_types ORDER BY asset_type').fetchall()
    current_asset_records=current_database_connection.execute(BILLBOARD_QUERY_SELECT+' ORDER BY billboard.asset_id').fetchall()
    current_snapshot_payload={'schemaVersion':1,'generation':current_generation_record['current_generation'],
        'types':{current_type_record['asset_type']:current_type_record['display_name'] for current_type_record in current_type_records},
        'assets':[build_billboard_response_record(current_asset_record) for current_asset_record in current_asset_records]}
    from asset_service.content_map_documents import read_map_document_records
    current_document_paths=current_database_connection.execute('SELECT document_path,document_revision FROM content.map_documents ORDER BY document_path').fetchall()
    current_snapshot_payload['mapDocuments']={current_path_record['document_path']:read_map_document_records(current_database_connection,current_path_record['document_path']) for current_path_record in current_document_paths}
    current_snapshot_payload['mapRevisions']={current_path_record['document_path']:current_path_record['document_revision'] for current_path_record in current_document_paths}
    current_building_records=current_database_connection.execute('SELECT * FROM content.building_properties ORDER BY asset_id').fetchall()
    current_snapshot_payload['buildingProperties']={current_building_record['asset_id']:{'anchorX':float(current_building_record['anchor_x']),'anchorY':float(current_building_record['anchor_y'])} for current_building_record in current_building_records}
    current_snapshot_payload['effects']=[current_effect_record['document'] for current_effect_record in current_database_connection.execute('SELECT document FROM content.effect_assets ORDER BY asset_id').fetchall()]
    validate_asset_export_boundary(current_snapshot_payload)
    current_payload_hash=hash_content_snapshot_payload(current_snapshot_payload)
    current_run_identifier=str(uuid4())
    current_database_connection.execute("""INSERT INTO content.snapshot_runs
        (run_id,content_generation,state,lease_owner,lease_expires_at,payload_sha256)
        VALUES(%s,%s,'running',%s,now()+(%s * interval '1 second'),%s)""",
        (current_run_identifier,current_snapshot_payload['generation'],'management-snapshot-worker',
         CONTENT_SNAPSHOT_LEASE_SECONDS,current_payload_hash))
    return {'needed':True,'runId':current_run_identifier,'payloadHash':current_payload_hash,'payload':current_snapshot_payload}


def complete_content_snapshot_export(current_database_connection,current_run_identifier,current_payload_hash,current_manifest_hash):
    current_database_connection.execute('SELECT * FROM content.generations WHERE singleton=true FOR UPDATE')
    current_run_record=current_database_connection.execute('SELECT * FROM content.snapshot_runs WHERE run_id=%s FOR UPDATE',
        (current_run_identifier,)).fetchone()
    if current_run_record is None or current_run_record['payload_sha256']!=current_payload_hash:
        raise ValueError('스냅샷 실행 또는 입력 해시가 일치하지 않습니다.')
    if current_run_record['state']=='completed':
        if current_run_record['manifest_sha256']!=current_manifest_hash:
            raise ContentRevisionConflict('완료된 스냅샷의 manifest가 변경되었습니다.')
        return {'completed':True,'generation':current_run_record['content_generation']}
    if current_run_record['state']!='running':
        raise ContentRevisionConflict('종료된 스냅샷은 새 실행으로 복구해야 합니다.')
    current_database_connection.execute("""UPDATE content.snapshot_runs SET state='completed',manifest_sha256=%s,
        finished_at=now(),lease_expires_at=NULL WHERE run_id=%s""",(current_manifest_hash,current_run_identifier))
    current_database_connection.execute('''UPDATE content.generations
        SET snapshot_generation=greatest(snapshot_generation,%s) WHERE singleton=true''',(current_run_record['content_generation'],))
    return {'completed':True,'generation':current_run_record['content_generation']}


def fail_content_snapshot_export(current_database_connection,current_run_identifier,current_error_message):
    current_database_connection.execute("""UPDATE content.snapshot_runs SET state='failed',error_message=%s,
        finished_at=now(),lease_expires_at=NULL WHERE run_id=%s AND state='running'""",
        (current_error_message[:2000],current_run_identifier))
    return {'failed':True}


def read_content_snapshot_health(current_database_connection):
    """관리자가 DB 저장 완료와 파일 반영 실패·정체를 구분할 수 있는 상태."""
    from asset_service.content_asset_queries import read_content_generation_state
    current_generation_state=read_content_generation_state(current_database_connection)
    current_latest_run=current_database_connection.execute("""
        SELECT run_id,content_generation,state,error_message,started_at,finished_at,
            lease_expires_at,(state='running' AND lease_expires_at<=now()) AS lease_expired
        FROM content.snapshot_runs ORDER BY started_at DESC,run_id DESC LIMIT 1
    """).fetchone()
    current_run_status=None
    if current_latest_run is not None:
        current_run_status={'runId':str(current_latest_run['run_id']),
            'generation':current_latest_run['content_generation'],'state':current_latest_run['state'],
            'error':current_latest_run['error_message'],'leaseExpired':bool(current_latest_run['lease_expired'])}
        for current_database_field,current_response_field in (
                ('started_at','startedAt'),('finished_at','finishedAt'),('lease_expires_at','leaseExpiresAt')):
            current_timestamp_value=current_latest_run[current_database_field]
            current_run_status[current_response_field]=current_timestamp_value.isoformat() if current_timestamp_value else None
    return {**current_generation_state,
        'pending':current_generation_state['generation']>current_generation_state['snapshotGeneration'],
        'latestSnapshot':current_run_status}
