"""명시적으로 승인받아 실행하는 단일 개발 서버 에셋 DB 초기 준비."""
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
from datetime import datetime, timezone
import psycopg
from psycopg import sql
from database.migrate import apply_asset_migrations, read_asset_configuration, read_asset_database_secret
from asset_service.connection import open_asset_service_connection, validate_asset_runtime_connection
from asset_service.content_asset_queries import read_content_generation_state

ASSET_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ASSET_OWNER_SECRET_PATH = ASSET_REPOSITORY_ROOT / '.local/secrets/asset_database_password'
ASSET_LOCAL_DATA_PATH = ASSET_REPOSITORY_ROOT / '.local/data/asset-postgres'


def prepare_local_secret_file(current_secret_path):
    current_secret_path.parent.mkdir(parents=True, exist_ok=True)
    if not current_secret_path.exists():
        current_file_descriptor = os.open(current_secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(current_file_descriptor, 'w') as current_secret_stream:
            current_secret_stream.write(secrets.token_urlsafe(48) + '\n')
            current_secret_stream.flush()
            os.fsync(current_secret_stream.fileno())
    return read_asset_database_secret(current_secret_path)


def initialize_local_asset_database():
    current_connection_values = read_asset_configuration(os.environ)
    current_runtime_role = current_connection_values['USER']
    current_runtime_secret_path = Path(current_connection_values['PASSWORD_FILE'])
    if (os.environ.get('ASSET_DB_STAGE') != 'local' or current_connection_values['HOST'] != '127.0.0.1'
            or current_connection_values['PORT'] != '25432' or current_connection_values['NAME'] != 'slime_assets_local'):
        raise ValueError('초기화는 local / 127.0.0.1:25432 / slime_assets_local 전용입니다.')
    if not re.fullmatch(r'[a-z][a-z0-9_]{2,62}', current_runtime_role) or current_runtime_role in ('asset_database_owner','asset_content_writer','asset_content_reader'):
        raise ValueError('독립 런타임 LOGIN 계정 이름을 지정하세요.')
    if not current_runtime_secret_path.is_absolute() or current_runtime_secret_path.resolve() == ASSET_OWNER_SECRET_PATH.resolve():
        raise ValueError('런타임 비밀번호는 owner와 다른 절대 경로 파일이어야 합니다.')
    if ASSET_LOCAL_DATA_PATH.is_symlink():
        raise ValueError('DB 데이터 경로에 심볼릭 링크를 허용하지 않습니다.')
    if ASSET_LOCAL_DATA_PATH.exists() and any(ASSET_LOCAL_DATA_PATH.iterdir()) and not ASSET_OWNER_SECRET_PATH.exists():
        raise ValueError('기존 DB의 owner 비밀 파일이 없습니다. 기존 비밀번호는 변경하지 않습니다.')
    current_owner_password = prepare_local_secret_file(ASSET_OWNER_SECRET_PATH)
    ASSET_LOCAL_DATA_PATH.mkdir(parents=True, exist_ok=True)
    print('에셋 전용 DB 준비 시작', flush=True)
    subprocess.run(['docker','compose','-f','docker-compose.database.yml','up','-d','--wait','--wait-timeout','60','asset-db'],cwd=ASSET_REPOSITORY_ROOT,check=True)
    with psycopg.connect(host='127.0.0.1',port=25432,dbname='slime_assets_local',user='asset_database_owner',password=current_owner_password,connect_timeout=5,autocommit=True) as current_owner_connection:
        apply_asset_migrations(current_owner_connection)
        current_existing_role = current_owner_connection.execute('SELECT rolcanlogin FROM pg_roles WHERE rolname=%s',(current_runtime_role,)).fetchone()
        if current_existing_role is None:
            current_runtime_password = prepare_local_secret_file(current_runtime_secret_path)
            with current_owner_connection.transaction():
                current_owner_connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(current_runtime_role),sql.Literal(current_runtime_password)))
                current_owner_connection.execute(sql.SQL('GRANT asset_content_writer TO {}').format(sql.Identifier(current_runtime_role)))
            print('런타임 계정 생성 완료',flush=True)
        elif not current_existing_role[0] or not current_runtime_secret_path.is_file():
            raise ValueError('기존 런타임 계정 또는 비밀 파일을 확인하세요. 자동 변경하지 않습니다.')
    with open_asset_service_connection() as current_runtime_connection:
        validate_asset_runtime_connection(current_runtime_connection)
        read_content_generation_state(current_runtime_connection)
    print('초기 준비 완료. 시드·백업·API 시작은 수행하지 않았습니다.',flush=True)


if __name__ == '__main__':
    current_stop_event = threading.Event()
    def emit_setup_heartbeat():
        while not current_stop_event.wait(5):
            print(f'{datetime.now(timezone.utc).isoformat()}/asset-setup/heartbeat DB 준비 진행 중',flush=True)
    current_heartbeat_thread = threading.Thread(target=emit_setup_heartbeat, daemon=True)
    current_heartbeat_thread.start()
    try:
        initialize_local_asset_database()
    finally:
        current_stop_event.set()
        current_heartbeat_thread.join()
