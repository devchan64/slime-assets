#!/usr/bin/env bash
# 개발 DB 초기 준비. 기존 데이터·비밀번호를 덮어쓰지 않는다.
set -Eeuo pipefail
umask 077
ASSET_SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 0 ]]; then
    printf '사용: ./scripts/setup_asset_local.sh (설정: .local/asset-service.env)\n'
    [[ $# -eq 1 && "$1" == --help ]] && exit 0
    exit 2
fi
cd "$ASSET_SCRIPT_ROOT"
source scripts/asset_server_environment.sh
load_asset_server_environment "${ASSET_SERVICE_ENV_FILE:-$ASSET_SCRIPT_ROOT/.local/asset-service.env}" "$ASSET_SCRIPT_ROOT"
ASSET_PYTHON_EXECUTABLE="${ASSET_PYTHON_EXECUTABLE:-$ASSET_SCRIPT_ROOT/.local/venvs/asset-service/bin/python}"
mkdir -p .local/logs/asset-service
current_setup_log="$ASSET_SCRIPT_ROOT/.local/logs/asset-service/setup-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
trap 'printf "%s/asset-setup/error 실패 위치=%s 로그=%s\n" "$(date -u +%FT%TZ)" "$LINENO" "$current_setup_log" >&2; tail -n 20 "$current_setup_log" >&2' ERR
printf '%s/asset-setup/start 개발 DB 초기 준비\n' "$(date -u +%FT%TZ)"
"$ASSET_PYTHON_EXECUTABLE" -m database.setup_local 2>&1 | tee "$current_setup_log"
printf '%s/asset-setup/complete 로그=%s\n' "$(date -u +%FT%TZ)" "$current_setup_log"
