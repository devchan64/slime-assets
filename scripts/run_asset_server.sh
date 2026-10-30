#!/usr/bin/env bash
# 단일 개발 서버의 독립 에셋 API 실행기. 스키마·계정 준비는 별도 수행한다.
set -Eeuo pipefail
umask 077

ASSET_SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
current_start_database=false
current_check_only=false
current_service_process=''
current_heartbeat_process=''

print_asset_server_help() {
    cat <<'HELP'
사용: scripts/run_asset_server.sh [--start-db] [--check]
  --start-db  준비된 로컬 Compose 에셋 DB를 시작한다.
  --check     설정·비밀 파일·DB 런타임 권한을 검사하고 종료한다.
설정 파일: .local/asset-service.env (ASSET_SERVICE_ENV_FILE로 경로 지정 가능)
파일 설정이 기존 셸 환경변수보다 우선합니다.
필수 설정:
  ASSET_DB_USER                       준비된 런타임 LOGIN 계정
  ASSET_DB_PASSWORD_FILE              DB 비밀번호 파일 절대 경로
  로컬 기본 모드(local-loopback)는 API 토큰 불필요
선택 환경변수:
  ASSET_PYTHON_EXECUTABLE  의존성이 설치된 Python (기본 .local/venvs/asset-service/bin/python)
  ASSET_API_LISTEN_PORT   API 포트 (기본 8791)
기본 DB: local / 127.0.0.1:25432 / slime_assets_local
마이그레이션·계정 생성·시드·백업은 자동 실행하지 않는다.
HELP
}
for current_argument_value in "$@"; do
    case "$current_argument_value" in
        --start-db) current_start_database=true ;;
        --check) current_check_only=true ;;
        --help|-h) print_asset_server_help; exit 0 ;;
        *) printf '지원하지 않는 인자: %s\n' "$current_argument_value" >&2; exit 2 ;;
    esac
done
cd "$ASSET_SCRIPT_ROOT"
source "$ASSET_SCRIPT_ROOT/scripts/asset_server_environment.sh"
load_asset_server_environment "${ASSET_SERVICE_ENV_FILE:-$ASSET_SCRIPT_ROOT/.local/asset-service.env}" "$ASSET_SCRIPT_ROOT"
ASSET_PYTHON_EXECUTABLE="${ASSET_PYTHON_EXECUTABLE:-$ASSET_SCRIPT_ROOT/.local/venvs/asset-service/bin/python}"
ASSET_API_LISTEN_PORT="${ASSET_API_LISTEN_PORT:-8791}"

mkdir -p .local/logs/asset-service
current_execution_log="$ASSET_SCRIPT_ROOT/.local/logs/asset-service/$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
exec > >(tee -a "$current_execution_log") 2>&1
emit_asset_server_trace() {
    printf '%s/asset-server/%s %s\n' "$(date -u +%FT%TZ)" "$1" "$2"
}
cleanup_asset_server_processes() {
    current_exit_status=$?
    trap - EXIT
    if [[ -n "$current_heartbeat_process" ]]; then
        kill "$current_heartbeat_process" 2>/dev/null || true
        wait "$current_heartbeat_process" 2>/dev/null || true
    fi
    if [[ -n "$current_service_process" ]]; then
        kill -TERM "$current_service_process" 2>/dev/null || true
        wait "$current_service_process" 2>/dev/null || true
    fi
    emit_asset_server_trace exit "종료 코드=$current_exit_status 로그=$current_execution_log"
    if (( current_exit_status != 0 )); then tail -n 20 "$current_execution_log" >&2; fi
    exit "$current_exit_status"
}
trap cleanup_asset_server_processes EXIT
trap 'emit_asset_server_trace error "실패 위치: ${BASH_SOURCE[0]}:${LINENO}"' ERR
trap 'exit 130' INT
trap 'exit 143' TERM

if ! command -v "$ASSET_PYTHON_EXECUTABLE" >/dev/null 2>&1; then
    emit_asset_server_trace error 'Python 실행 환경이 없습니다. ./scripts/install_asset_dependencies.sh를 먼저 실행하세요.'
    exit 1
fi
export ASSET_DB_STAGE="${ASSET_DB_STAGE:-local}"
export ASSET_DB_HOST="${ASSET_DB_HOST:-127.0.0.1}"
export ASSET_DB_PORT="${ASSET_DB_PORT:-25432}"
export ASSET_DB_NAME="${ASSET_DB_NAME:-slime_assets_local}"
export ASSET_API_LISTEN_PORT
current_missing_settings=()
for current_setting_name in ASSET_DB_USER ASSET_DB_PASSWORD_FILE; do
    if [[ -z "${!current_setting_name:-}" ]]; then
        current_missing_settings+=("$current_setting_name")
    fi
done
if (( ${#current_missing_settings[@]} )); then
    emit_asset_server_trace configuration "필수 설정 누락: ${current_missing_settings[*]}"
    printf '%s\n'         '준비된 런타임 LOGIN 계정과 비밀 파일을 다음 환경변수로 지정하세요.'         '  export ASSET_DB_USER="<런타임 계정>"'         '  export ASSET_DB_PASSWORD_FILE="<DB 비밀번호 파일 절대경로>"'         '계정·비밀 파일이 아직 없다면 database/README.md의 초기 준비가 먼저 필요합니다.'         '이 스크립트는 DB 계정·비밀 파일을 자동 생성하지 않습니다.'
    exit 2
fi
(
    while sleep 5; do
        emit_asset_server_trace heartbeat "준비 또는 API 실행 중 로그 바이트=$(wc -c < "$current_execution_log")"
    done
) &
current_heartbeat_process=$!
emit_asset_server_trace validate '개발 연결 설정·의존성·비밀 파일 검사'
"$ASSET_PYTHON_EXECUTABLE" - <<'PY'
import os
from pathlib import Path
import uvicorn
from database.migrate import read_asset_configuration, read_asset_database_secret
from asset_service.api import ASSET_INTERNAL_API_APP
if os.environ['ASSET_DB_STAGE'] != 'local':
    raise ValueError('이 실행기는 단일 개발 서버의 local 단계 전용입니다.')
current_connection_values = read_asset_configuration(os.environ)
current_secret_variables = ['ASSET_DB_PASSWORD_FILE']
if os.environ.get('ASSET_SERVICE_AUTH_MODE', 'token') != 'local-loopback':
    current_secret_variables.append('SLIME_CONTENT_SERVICE_SECRET_FILE')
for current_variable_name in current_secret_variables:
    current_secret_path = os.environ.get(current_variable_name, '')
    if not current_secret_path or not Path(current_secret_path).is_absolute():
        raise ValueError(current_variable_name + '에 비밀 파일 절대 경로가 필요합니다.')
    read_asset_database_secret(current_secret_path)
current_listen_port = os.environ['ASSET_API_LISTEN_PORT']
if not current_listen_port.isascii() or not current_listen_port.isdecimal() or not 1 <= int(current_listen_port) <= 65535:
    raise ValueError('API 포트는 1~65535 정수여야 합니다.')
PY
if "$current_start_database"; then
    if [[ "$ASSET_DB_HOST" != 127.0.0.1 || "$ASSET_DB_PORT" != 25432 ]]; then
        emit_asset_server_trace error '--start-db는 127.0.0.1:25432 Compose 연결만 지원합니다.'
        exit 2
    fi
    emit_asset_server_trace database '에셋 DB 시작·준비 대기 (최대 60초)'
    docker compose -f docker-compose.database.yml up -d --wait --wait-timeout 60 asset-db
fi
emit_asset_server_trace preflight 'DB 접속·런타임 권한·콘텐츠 스키마 검사'
"$ASSET_PYTHON_EXECUTABLE" - <<'PY'
from asset_service.connection import open_asset_service_connection, validate_asset_runtime_connection
from asset_service.content_asset_queries import read_content_generation_state
with open_asset_service_connection() as current_database_connection:
    validate_asset_runtime_connection(current_database_connection)
    read_content_generation_state(current_database_connection)
PY
if "$current_check_only"; then
    emit_asset_server_trace ready '사전 검사 통과 (API 미실행)'
    exit 0
fi
emit_asset_server_trace start "에셋 API http://127.0.0.1:$ASSET_API_LISTEN_PORT 시작"
"$ASSET_PYTHON_EXECUTABLE" -m uvicorn asset_service.api:ASSET_INTERNAL_API_APP --host 127.0.0.1 --no-proxy-headers --port "$ASSET_API_LISTEN_PORT" &
current_service_process=$!
wait "$current_service_process"
