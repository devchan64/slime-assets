#!/usr/bin/env bash
# 에셋 개발 환경의 공통 명령 진입점.
set -Eeuo pipefail
asset_command_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
print_asset_command_help() {
    cat <<'HELP'
사용: ./scripts/asset.sh <dev|service> <명령> [옵션]

개발 환경 (dev):
  install  API 전용 Python 환경과 의존성을 설치합니다.
  setup    로컬 DB·계정·마이그레이션을 준비합니다.
  check    설정·DB 권한·원장 동기화를 검사하고 DB 변경을 롤백합니다.
서비스 실행 (service):
  start    원장을 DB에 동기화하고 로컬 개발 API를 실행합니다.

  help     전체 또는 지정한 명령의 도움말을 표시합니다.

도움말: ./scripts/asset.sh help [dev|service [명령]]
이 서비스는 local/dev 전용이며 운영(prod) 실행은 지원하지 않습니다.
상세 안내: asset_service/README.md
HELP
}
print_asset_subcommand_help() {
    case "$1" in
        dev) printf '%s\n' '사용: ./scripts/asset.sh dev <install|setup|check> [옵션]' '개발 환경 설치·초기화·검사를 수행합니다.' ;;
        service) printf '%s\n' '사용: ./scripts/asset.sh service start [--start-db]' '로컬 개발 API를 실행합니다. 운영(prod) 실행은 지원하지 않습니다.' ;;
        install) printf '%s\n' '사용: ./scripts/asset.sh dev install' '전용 가상환경에 의존성을 설치합니다. ASSET_BOOTSTRAP_PYTHON으로 Python을 지정할 수 있습니다.' ;;
        setup) printf '%s\n' '사용: ./scripts/asset.sh dev setup' '로컬 Compose DB, 계정과 비밀 파일을 준비하고 마이그레이션합니다. 영구 DB와 계정이 생성될 수 있습니다.' ;;
        check) printf '%s\n' '사용: ./scripts/asset.sh dev check [--start-db]' '설정·DB 권한·원장을 검사하고 DB 변경을 롤백합니다. 검사 중 쓰기 잠금을 사용합니다.' '  --start-db  준비된 로컬 Compose DB를 시작합니다.' ;;
        start) printf '%s\n' '사용: ./scripts/asset.sh service start [--start-db]' 'Git 추적 원장을 DB에 동기화하고 로컬 API를 전경 실행합니다.' '  --start-db  준비된 로컬 Compose DB를 시작합니다.' ;;
        *) printf '알 수 없는 명령: %s\n' "$1" >&2; return 2 ;;
    esac
}
# 환경 파일을 데이터로 읽는다. source/eval로 파일 내용을 실행하지 않는다.
load_asset_server_environment() {
    local current_config_path="$1"
    local current_repository_root="$2"
    local current_config_line current_setting_name current_setting_value
    local current_line_number=0
    local -A current_seen_settings=()
    if [[ ! -f "$current_config_path" || -L "$current_config_path" ]]; then
        printf '환경 파일이 없거나 일반 파일이 아닙니다: %s\n' "$current_config_path" >&2
        return 2
    fi
    while IFS= read -r current_config_line || [[ -n "$current_config_line" ]]; do
        current_line_number=$((current_line_number + 1))
        current_config_line="${current_config_line%$'\r'}"
        [[ "$current_config_line" =~ ^[[:space:]]*(#|$) ]] && continue
        if [[ ! "$current_config_line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
            printf '환경 파일 %s행: KEY=value 형식이 필요합니다.\n' "$current_line_number" >&2
            return 2
        fi
        current_setting_name="${BASH_REMATCH[1]}"
        current_setting_value="${BASH_REMATCH[2]}"
        case "$current_setting_name" in
            ASSET_SERVICE_AUTH_MODE|ASSET_DB_STAGE|ASSET_DB_HOST|ASSET_DB_PORT|ASSET_DB_NAME|ASSET_DB_USER|ASSET_DB_PASSWORD_FILE|SLIME_CONTENT_SERVICE_SECRET_FILE|ASSET_PYTHON_EXECUTABLE|ASSET_API_LISTEN_PORT|ASSET_SERVICE_ALLOWED_HOSTS) ;;
            *) printf '환경 파일 %s행: 허용하지 않는 설정입니다. 비밀값은 별도 파일에 보관하세요.\n' "$current_line_number" >&2; return 2 ;;
        esac
        if [[ -v "current_seen_settings[$current_setting_name]" || -z "$current_setting_value" ]]; then
            printf '환경 파일 %s행: 중복 키 또는 빈 값입니다.\n' "$current_line_number" >&2
            return 2
        fi
        if [[ "$current_setting_value" == *'$'* || "$current_setting_value" == *'`'* || "$current_setting_value" == *'"'* || "$current_setting_value" == *"'"* ]]; then
            printf '환경 파일 %s행: 따옴표·변수 치환·명령 치환을 지원하지 않습니다. 값을 그대로 적으세요.\n' "$current_line_number" >&2
            return 2
        fi
        case "$current_setting_name" in
            ASSET_DB_PASSWORD_FILE|SLIME_CONTENT_SERVICE_SECRET_FILE|ASSET_PYTHON_EXECUTABLE)
                [[ "$current_setting_value" == /* ]] || current_setting_value="$current_repository_root/$current_setting_value" ;;
        esac
        current_seen_settings[$current_setting_name]=1
        export "$current_setting_name=$current_setting_value"
    done < "$current_config_path"
}

run_asset_install_command() (
# 시스템 Python을 변경하지 않고 에셋 API 전용 환경을 준비한다.
set -Eeuo pipefail
umask 077

ASSET_SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ASSET_BOOTSTRAP_PYTHON="${ASSET_BOOTSTRAP_PYTHON:-python3}"
ASSET_SERVICE_VENV_PATH="$ASSET_SCRIPT_ROOT/.local/venvs/asset-service"
current_heartbeat_process=''

if [[ $# -gt 0 ]]; then
    if [[ $# -eq 1 && ( "$1" == --help || "$1" == -h ) ]]; then
        printf '%s\n' '사용: ./scripts/asset.sh dev install' \
            '선택: ASSET_BOOTSTRAP_PYTHON=/절대/경로/python3' \
            '설치 위치: .local/venvs/asset-service (기존 환경은 재사용)' \
            'DB·API 기동이나 비밀 파일 생성은 수행하지 않습니다.'
        exit 0
    fi
    printf '지원하지 않는 인자입니다. --help를 확인하세요.\n' >&2
    exit 2
fi
cd "$ASSET_SCRIPT_ROOT"
mkdir -p .local/logs/asset-service
current_install_log="$ASSET_SCRIPT_ROOT/.local/logs/asset-service/install-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
exec > >(tee -a "$current_install_log") 2>&1
emit_dependency_install_trace() {
    printf '%s/asset-dependencies/%s %s\n' "$(date -u +%FT%TZ)" "$1" "$2"
}
cleanup_dependency_install_process() {
    current_exit_status=$?
    trap - EXIT
    if [[ -n "$current_heartbeat_process" ]]; then
        kill "$current_heartbeat_process" 2>/dev/null || true
        wait "$current_heartbeat_process" 2>/dev/null || true
    fi
    emit_dependency_install_trace exit "종료 코드=$current_exit_status 로그=$current_install_log"
    if (( current_exit_status != 0 )); then tail -n 20 "$current_install_log" >&2; fi
    exit "$current_exit_status"
}
trap cleanup_dependency_install_process EXIT
trap 'emit_dependency_install_trace error "실패 위치: ${BASH_SOURCE[0]}:${LINENO}"' ERR
trap 'exit 130' INT
trap 'exit 143' TERM
(
    while sleep 5; do
        emit_dependency_install_trace heartbeat "설치 진행 중 로그 바이트=$(wc -c < "$current_install_log")"
    done
) &
current_heartbeat_process=$!
emit_dependency_install_trace prepare 'Python·가상환경 지원 확인'
"$ASSET_BOOTSTRAP_PYTHON" - <<'PY'
import sys
import venv
if sys.version_info < (3, 10):
    raise RuntimeError('Python 3.10 이상이 필요합니다.')
PY
if [[ ! -e "$ASSET_SERVICE_VENV_PATH" ]]; then
    emit_dependency_install_trace venv '에셋 API 전용 가상환경 생성'
    if ! "$ASSET_BOOTSTRAP_PYTHON" -m venv "$ASSET_SERVICE_VENV_PATH"; then
        emit_dependency_install_trace error 'venv 생성 실패. Ubuntu의 선택한 Python에 맞는 venv 패키지(python3-venv 등)를 설치한 뒤 재시도하세요.'
        exit 1
    fi
fi
if [[ ! -f "$ASSET_SERVICE_VENV_PATH/pyvenv.cfg" || ! -x "$ASSET_SERVICE_VENV_PATH/bin/python" ]]; then
    emit_dependency_install_trace error '기존 가상환경이 불완전합니다. .local/venvs/asset-service를 확인하세요. 자동 삭제하지 않습니다.'
    exit 1
fi
emit_dependency_install_trace install 'requirements의 고정 버전 설치'
"$ASSET_SERVICE_VENV_PATH/bin/python" -m pip install --disable-pip-version-check -r asset_service/requirements.txt
emit_dependency_install_trace verify '의존성 충돌·API import 검사'
"$ASSET_SERVICE_VENV_PATH/bin/python" -m pip check
"$ASSET_SERVICE_VENV_PATH/bin/python" - <<'PY'
import fastapi
import psycopg
import uvicorn
from asset_service.api import ASSET_INTERNAL_API_APP
print('FastAPI·uvicorn·psycopg 및 에셋 API import 정상 (DB 미접속)', flush=True)
PY
emit_dependency_install_trace complete '설치 완료. DB 설정 후 ./scripts/asset.sh dev check를 실행하세요.'
)

run_asset_setup_command() (
# 개발 DB 초기 준비. 기존 데이터·비밀번호를 덮어쓰지 않는다.
set -Eeuo pipefail
umask 077
ASSET_SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 0 ]]; then
    printf '사용: ./scripts/asset.sh dev setup (설정: .local/asset-service.env)\n'
    [[ $# -eq 1 && "$1" == --help ]] && exit 0
    exit 2
fi
cd "$ASSET_SCRIPT_ROOT"
default_asset_environment_path="$ASSET_SCRIPT_ROOT/.local/asset-service.env"
if [[ -z "${ASSET_SERVICE_ENV_FILE:-}" && ! -e "$default_asset_environment_path" && ! -L "$default_asset_environment_path" ]]; then
    mkdir -p .local
    temporary_asset_environment_path="$(mktemp .local/.asset-service.env.XXXXXXXX)"
    trap 'rm -f -- "$temporary_asset_environment_path"' EXIT
    cat config/asset-service.env.example > "$temporary_asset_environment_path"
    if ln -T -- "$temporary_asset_environment_path" "$default_asset_environment_path"; then
        printf '기본 환경 파일 생성: %s\n' "$default_asset_environment_path"
    elif [[ ! -f "$default_asset_environment_path" || -L "$default_asset_environment_path" ]]; then
        exit 2
    fi
    rm -f -- "$temporary_asset_environment_path"
    trap - EXIT
fi
load_asset_server_environment "${ASSET_SERVICE_ENV_FILE:-$default_asset_environment_path}" "$ASSET_SCRIPT_ROOT"
ASSET_PYTHON_EXECUTABLE="${ASSET_PYTHON_EXECUTABLE:-$ASSET_SCRIPT_ROOT/.local/venvs/asset-service/bin/python}"
mkdir -p .local/logs/asset-service
current_setup_log="$ASSET_SCRIPT_ROOT/.local/logs/asset-service/setup-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
trap 'printf "%s/asset-setup/error 실패 위치=%s 로그=%s\n" "$(date -u +%FT%TZ)" "$LINENO" "$current_setup_log" >&2; tail -n 20 "$current_setup_log" >&2' ERR
printf '%s/asset-setup/start 개발 DB 초기 준비\n' "$(date -u +%FT%TZ)"
"$ASSET_PYTHON_EXECUTABLE" -m database.setup_local 2>&1 | tee "$current_setup_log"
printf '%s/asset-setup/complete 로그=%s\n' "$(date -u +%FT%TZ)" "$current_setup_log"
)

run_asset_start_command() (
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
사용: scripts/asset.sh service start [--start-db] 또는 scripts/asset.sh dev check [--start-db]
  --start-db  준비된 로컬 Compose 에셋 DB를 시작한다.
  --check     설정·권한·원장 동기화를 검증하고 DB 변경은 롤백한다.
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
매 실행 시 Git 추적 원장을 DB에 동기화한다. 미반영 DB 편집은 시작을 중단한다.
마이그레이션·계정 생성·백업은 자동 실행하지 않는다.
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
    emit_asset_server_trace error 'Python 실행 환경이 없습니다. ./scripts/asset.sh dev install를 먼저 실행하세요.'
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
emit_asset_server_trace ledger "Git 원장 검증·DB 동기화 (check=$current_check_only)"
if "$current_check_only"; then
    "$ASSET_PYTHON_EXECUTABLE" -m asset_service.ledger_sync --check
else
    "$ASSET_PYTHON_EXECUTABLE" -m asset_service.ledger_sync
fi
if "$current_check_only"; then
    emit_asset_server_trace ready '사전 검사 통과 (API 미실행)'
    exit 0
fi
emit_asset_server_trace start "에셋 API http://127.0.0.1:$ASSET_API_LISTEN_PORT 시작"
"$ASSET_PYTHON_EXECUTABLE" -m uvicorn asset_service.api:ASSET_INTERNAL_API_APP --host 127.0.0.1 --no-proxy-headers --port "$ASSET_API_LISTEN_PORT" &
current_service_process=$!
wait "$current_service_process"
)

if (( $# == 0 )); then print_asset_command_help; exit 0; fi
asset_requested_group="$1"
shift
if [[ "$asset_requested_group" == help || "$asset_requested_group" == --help || "$asset_requested_group" == -h ]]; then
    if (( $# == 0 )); then print_asset_command_help; exit 0; fi
    asset_requested_group="$1"
    shift
    if (( $# == 0 )); then print_asset_subcommand_help "$asset_requested_group"; exit $?; fi
    if (( $# != 1 )); then printf '사용: ./scripts/asset.sh help [dev|service [명령]]\n' >&2; exit 2; fi
    case "$asset_requested_group:$1" in
        dev:install|dev:setup|dev:check|service:start) print_asset_subcommand_help "$1"; exit $? ;;
        *) printf '알 수 없는 명령: %s %s\n' "$asset_requested_group" "$1" >&2; exit 2 ;;
    esac
fi
case "$asset_requested_group" in
    dev|service) ;;
    *) printf '알 수 없는 명령 그룹: %s (도움말: ./scripts/asset.sh --help)\n' "$asset_requested_group" >&2; exit 2 ;;
esac
if (( $# == 0 )); then print_asset_subcommand_help "$asset_requested_group"; exit 0; fi
if (( $# == 1 )) && [[ "$1" == --help || "$1" == -h ]]; then print_asset_subcommand_help "$asset_requested_group"; exit 0; fi
asset_requested_command="$1"
shift
case "$asset_requested_group:$asset_requested_command" in
    dev:install|dev:setup|dev:check|service:start) ;;
    *) printf '알 수 없는 명령: %s %s\n' "$asset_requested_group" "$asset_requested_command" >&2; exit 2 ;;
esac
if (( $# == 1 )) && [[ "$1" == --help || "$1" == -h ]]; then
    print_asset_subcommand_help "$asset_requested_command"
    exit 0
fi
case "$asset_requested_command" in
    install|setup)
        if (( $# != 0 )); then printf '지원하지 않는 옵션입니다. 명령 도움말을 확인하세요.\n' >&2; exit 2; fi
        if [[ "$asset_requested_command" == install ]]; then run_asset_install_command; exit $?; fi
        run_asset_setup_command ;;
    check|start)
        if (( $# > 1 )) || { (( $# == 1 )) && [[ "$1" != --start-db ]]; }; then
            printf '지원하지 않는 옵션입니다. 명령 도움말을 확인하세요.\n' >&2; exit 2
        fi
        if [[ "$asset_requested_command" == check ]]; then run_asset_start_command --check "$@"; exit $?; fi
        run_asset_start_command "$@" ;;
esac
