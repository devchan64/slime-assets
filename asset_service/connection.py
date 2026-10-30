"""에셋 전용 접속. 게임 DB 연결과 자동 마이그레이션을 사용하지 않는다."""
import os
import psycopg
from psycopg.rows import dict_row
from database.migrate import read_asset_configuration, read_asset_database_secret, ASSET_CONNECTION_TIMEOUT


def open_asset_service_connection():
    current_connection_values = read_asset_configuration(os.environ)
    if os.environ.get('ASSET_DB_STAGE', 'local') == 'prod':
        raise ValueError('편집 API는 local/dev에서만 실행합니다. 운영은 배포된 원장을 사용하세요.')
    return psycopg.connect(host=current_connection_values['HOST'], port=int(current_connection_values['PORT']),
        dbname=current_connection_values['NAME'], user=current_connection_values['USER'],
        password=read_asset_database_secret(current_connection_values['PASSWORD_FILE']),
        sslmode=current_connection_values['SSLMODE'], connect_timeout=ASSET_CONNECTION_TIMEOUT,
        row_factory=dict_row, options='-c statement_timeout=20000 -c lock_timeout=5000')


def validate_asset_runtime_connection(current_database_connection):
    current_role_record = current_database_connection.execute('''SELECT current_database() AS database_name,
        rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user''').fetchone()
    if current_role_record['database_name'] not in ('slime_assets_local','slime_assets_dev'):
        raise ValueError('개발 에셋 DB만 사용할 수 있습니다.')
    if any(current_role_record[current_role_flag] for current_role_flag in ('rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')):
        raise ValueError('관리자 DB 역할로 API를 실행할 수 없습니다. 전용 런타임 계정을 사용하세요.')
    if current_database_connection.execute("SELECT nspname FROM pg_namespace WHERE nspname IN ('game','identity')").fetchone():
        raise ValueError('게임·Identity 스키마가 있는 DB에는 연결할 수 없습니다.')
    current_permission_record = current_database_connection.execute("""SELECT
        has_table_privilege(current_user,'content.generations','UPDATE') AS can_write,
        has_schema_privilege(current_user,'content','CREATE') AS can_create""").fetchone()
    if not current_permission_record['can_write'] or current_permission_record['can_create']:
        raise ValueError('에셋 쓰기 역할의 권한 구성을 확인하세요.')
