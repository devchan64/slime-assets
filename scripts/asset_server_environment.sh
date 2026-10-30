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
