#!/usr/bin/env bash
# 시스템 Python을 변경하지 않고 에셋 API 전용 환경을 준비한다.
set -Eeuo pipefail
umask 077

ASSET_SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ASSET_BOOTSTRAP_PYTHON="${ASSET_BOOTSTRAP_PYTHON:-python3}"
ASSET_SERVICE_VENV_PATH="$ASSET_SCRIPT_ROOT/.local/venvs/asset-service"
current_heartbeat_process=''

if [[ $# -gt 0 ]]; then
    if [[ $# -eq 1 && ( "$1" == --help || "$1" == -h ) ]]; then
        printf '%s\n' '사용: ./scripts/install_asset_dependencies.sh' \
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
emit_dependency_install_trace complete '설치 완료. DB 설정 후 ./scripts/run_asset_server.sh --check를 실행하세요.'
