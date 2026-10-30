"""에셋 전용 DB만 초기화한다. 게임 서비스나 파일 시드를 실행하지 않는다."""
import hashlib
import os
import re
import stat
import traceback
from datetime import datetime, timezone
from pathlib import Path

import psycopg

ASSET_MIGRATION_ROOT = Path(__file__).parent / 'migrations'
ASSET_DATABASE_LOCK = 734619205
ASSET_DATABASE_STAGES = ('local', 'dev', 'prod')
ASSET_CONNECTION_TIMEOUT = 5
ASSET_SECRET_MAXIMUM_BYTES = 4096


def emit_database_trace(current_stage_name, current_message_text):
    print(f'{datetime.now(timezone.utc).isoformat()}/asset-database/{current_stage_name} {current_message_text}', flush=True)


def read_asset_configuration(current_environment_values):
    """local은 루프백만 허용하고 공용 DB 설정으로 대체 연결하지 않는다."""
    current_stage_name = current_environment_values.get('ASSET_DB_STAGE', 'local')
    if current_stage_name not in ASSET_DATABASE_STAGES:
        raise ValueError('ASSET_DB_STAGE는 local/dev/prod만 허용합니다.')
    current_connection_values = {}
    for current_setting_name in ('HOST', 'PORT', 'NAME', 'USER', 'PASSWORD_FILE'):
        current_setting_value = current_environment_values.get('ASSET_DB_' + current_setting_name, '')
        if not current_setting_value or current_setting_value != current_setting_value.strip():
            raise ValueError('에셋 DB 설정 누락 또는 공백 오류: ASSET_DB_' + current_setting_name)
        current_connection_values[current_setting_name] = current_setting_value
    if current_connection_values['NAME'] != 'slime_assets_' + current_stage_name:
        raise ValueError('DB 이름은 slime_assets_<stage>여야 합니다. 기존 게임 DB는 사용할 수 없습니다.')
    current_port_value = current_connection_values['PORT']
    if not current_port_value.isascii() or not current_port_value.isdecimal() or not 1 <= int(current_port_value) <= 65535:
        raise ValueError('ASSET_DB_PORT는 1~65535 정수여야 합니다.')
    if current_stage_name == 'local' and current_connection_values['HOST'] not in ('127.0.0.1', '::1', 'localhost'):
        raise ValueError('local 에셋 DB는 루프백 호스트만 허용합니다.')
    current_connection_values['SSLMODE'] = 'disable' if current_stage_name == 'local' else 'verify-full'
    return current_connection_values


def read_asset_database_secret(current_secret_path):
    current_secret_file = Path(current_secret_path)
    current_secret_stat = current_secret_file.lstat()
    if not stat.S_ISREG(current_secret_stat.st_mode) or current_secret_stat.st_mode & 0o077:
        raise ValueError('DB 비밀 파일은 소유자만 접근 가능한 일반 파일이어야 합니다.')
    if not 0 < current_secret_stat.st_size <= ASSET_SECRET_MAXIMUM_BYTES:
        raise ValueError('DB 비밀 파일 크기가 올바르지 않습니다.')
    current_secret_value = current_secret_file.read_text(encoding='utf-8').rstrip('\r\n')
    if not current_secret_value or '\n' in current_secret_value or '\r' in current_secret_value or '\x00' in current_secret_value:
        raise ValueError('DB 비밀 파일에는 한 줄의 비밀번호가 필요합니다.')
    return current_secret_value


def load_asset_migrations(current_migration_root):
    current_migration_records = []
    for current_migration_path in sorted(current_migration_root.iterdir()):
        if not re.fullmatch(r'[0-9]{3}_[a-z_]+\.sql', current_migration_path.name) or not current_migration_path.is_file():
            raise ValueError('지원하지 않는 마이그레이션 파일: ' + current_migration_path.name)
        current_migration_bytes = current_migration_path.read_bytes()
        current_migration_records.append((current_migration_path.name, hashlib.sha256(current_migration_bytes).hexdigest(), current_migration_bytes.decode('utf-8')))
    if not current_migration_records:
        raise ValueError('마이그레이션 파일이 없습니다.')
    current_version_numbers = [int(current_record_values[0][:3]) for current_record_values in current_migration_records]
    if current_version_numbers != list(range(1, len(current_version_numbers) + 1)):
        raise ValueError('마이그레이션 번호는 001부터 연속되고 중복되지 않아야 합니다.')
    return current_migration_records


def apply_asset_migrations(current_database_connection, current_migration_root=ASSET_MIGRATION_ROOT):
    current_migration_records = load_asset_migrations(current_migration_root)
    with current_database_connection.transaction():
        current_database_connection.execute("SET LOCAL lock_timeout = '5s'")
        current_database_connection.execute("SET LOCAL statement_timeout = '5s'")
        current_database_connection.execute('SELECT pg_advisory_xact_lock(%s)', (ASSET_DATABASE_LOCK,))
        current_database_name = current_database_connection.execute('SELECT current_database()').fetchone()[0]
        if current_database_name not in tuple('slime_assets_' + current_stage_name for current_stage_name in ASSET_DATABASE_STAGES):
            raise ValueError('에셋 전용 DB 이름이 아닙니다.')
        current_foreign_schemas = current_database_connection.execute("SELECT nspname FROM pg_namespace WHERE nspname IN ('game','identity')").fetchall()
        if current_foreign_schemas:
            raise ValueError('게임·Identity 스키마가 있는 DB에는 적용할 수 없습니다.')
        current_tracking_exists = current_database_connection.execute("SELECT to_regclass('asset_admin.schema_migrations')").fetchone()[0]
        if not current_tracking_exists and current_database_connection.execute("SELECT to_regnamespace('content')").fetchone()[0]:
            raise ValueError('관리 이력이 없는 기존 content 스키마는 자동 인수하지 않습니다.')
        current_database_connection.execute('CREATE SCHEMA IF NOT EXISTS asset_admin')
        current_database_connection.execute('REVOKE ALL ON SCHEMA asset_admin FROM PUBLIC')
        current_database_connection.execute('''CREATE TABLE IF NOT EXISTS asset_admin.schema_migrations (
            migration_name text PRIMARY KEY, migration_sha256 text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT now())''')
        current_applied_records = dict(current_database_connection.execute('SELECT migration_name,migration_sha256 FROM asset_admin.schema_migrations').fetchall())
        current_expected_records = {current_record_values[0]: current_record_values[1] for current_record_values in current_migration_records}
        if any(current_expected_records.get(current_record_name) != current_record_hash for current_record_name, current_record_hash in current_applied_records.items()):
            raise ValueError('적용된 마이그레이션의 해시 불일치 또는 알 수 없는 버전입니다.')
        if sorted(current_applied_records) != [current_record_values[0] for current_record_values in current_migration_records[:len(current_applied_records)]]:
            raise ValueError('적용된 마이그레이션 이력에 중간 누락이 있습니다.')
        for current_migration_name, current_migration_hash, current_migration_source in current_migration_records:
            if current_migration_name in current_applied_records:
                continue
            emit_database_trace('apply', current_migration_name)
            current_database_connection.execute(current_migration_source)
            current_database_connection.execute('INSERT INTO asset_admin.schema_migrations(migration_name,migration_sha256) VALUES(%s,%s)', (current_migration_name, current_migration_hash))
        current_database_connection.execute('REVOKE ALL ON SCHEMA content FROM PUBLIC')


def run_asset_migrations():
    emit_database_trace('start', '접속 설정·스키마 검증 시작')
    current_connection_values = read_asset_configuration(os.environ)
    current_secret_value = read_asset_database_secret(current_connection_values['PASSWORD_FILE'])
    with psycopg.connect(host=current_connection_values['HOST'], port=int(current_connection_values['PORT']),
            dbname=current_connection_values['NAME'], user=current_connection_values['USER'],
            password=current_secret_value, sslmode=current_connection_values['SSLMODE'],
            connect_timeout=ASSET_CONNECTION_TIMEOUT, autocommit=True) as current_database_connection:
        apply_asset_migrations(current_database_connection)
    emit_database_trace('complete', '마이그레이션 커밋 완료')


if __name__ == '__main__':
    try:
        run_asset_migrations()
    except Exception:
        emit_database_trace('failure', '실패 원인·행은 아래 traceback을 확인하세요.')
        traceback.print_exc()
        raise SystemExit(1)
