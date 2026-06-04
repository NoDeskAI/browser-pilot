#!/usr/bin/env bash
set -euo pipefail

VERSION="0.0.1"
API_URL="{{API_URL}}"
CLI_NAME="{{CLI_NAME}}"
CONFIG_DIR="${HOME}/.${CLI_NAME}"
CONFIG_FILE="${CONFIG_DIR}/config.json"
LEASE_MAX_TTL_SECONDS=1800

# ── Globals (set by option parser) ────────────────────────────────────
JSON_OUT=false
API_URL_OPT=""
SESSION_OPT=""

# ── Helpers ───────────────────────────────────────────────────────────

_load_config() {
  [[ -f "$CONFIG_FILE" ]] || return 0
  local v
  v=$(grep -o '"api_url" *: *"[^"]*"' "$CONFIG_FILE" 2>/dev/null | head -1 | sed 's/.*: *"//;s/"//') || true
  if [[ -n "$v" ]]; then
    API_URL="$v"
  fi
  if [[ -n "${BPILOT_API_URL:-}" ]]; then
    API_URL="$BPILOT_API_URL"
  fi
  return 0
}

_config_env_get() {
  case "$1" in
    api_url) printf '%s' "${BPILOT_API_URL:-}" ;;
    api_token) printf '%s' "${BPILOT_API_TOKEN:-}" ;;
    active_session) printf '%s' "${BPILOT_ACTIVE_SESSION:-}" ;;
    *) printf '' ;;
  esac
}

_config_get() {
  local env_v
  env_v="$(_config_env_get "$1")"
  if [[ -n "$env_v" ]]; then
    printf '%s' "$env_v"
    return
  fi
  [[ -f "$CONFIG_FILE" ]] || { echo ""; return; }
  grep -o "\"$1\" *: *\"[^\"]*\"" "$CONFIG_FILE" 2>/dev/null | head -1 | sed 's/.*: *"//;s/"//' || echo ""
}

_config_save() {
  mkdir -p "$CONFIG_DIR"
  printf '%s\n' "$1" > "$CONFIG_FILE"
}

_config_set_key() {
  local key="$1" val="$2"
  local cfg
  if [[ -f "$CONFIG_FILE" ]]; then
    cfg=$(cat "$CONFIG_FILE")
  else
    cfg='{"api_url":"","active_session":""}'
  fi
  mkdir -p "$CONFIG_DIR"
  if echo "$cfg" | grep -q "\"$key\""; then
    echo "$cfg" | sed "s|\"$key\" *: *\"[^\"]*\"|\"$key\":\"$val\"|" > "$CONFIG_FILE"
  else
    echo "$cfg" | sed "s|}|,\"$key\":\"$val\"}|" > "$CONFIG_FILE"
  fi
}

_esc() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\r'/\\r}"
  printf '%s' "$s"
}

_egress_json_value() {
  local egress_id="$1"
  if [[ "$egress_id" == "direct" ]]; then
    printf 'null'
  else
    printf '"%s"' "$(_esc "$egress_id")"
  fi
}

_print_or_fail_ok() {
  local resp="$1"
  if echo "$resp" | grep -Eq '"ok"[[:space:]]*:[[:space:]]*false'; then
    echo "$resp" | _out
    exit 1
  fi
  echo "$resp" | _out
}

_resolve_url() {
  local u="${API_URL_OPT:-${BPILOT_API_URL:-$API_URL}}"
  printf '%s' "${u%/}"
}

_sid() {
  local s="${SESSION_OPT:-$(_config_get active_session)}"
  if [[ -z "$s" ]]; then
    echo "Error: No session target. Pass --session <id> or run: $CLI_NAME session use <id>" >&2
    exit 1
  fi
  printf '%s' "$s"
}

_api_get() {
  local path="$1"; shift
  local token
  token="$(_config_get api_token)"
  if [[ -n "$token" ]]; then
    curl -sS -H "Authorization: Bearer $token" "$(_resolve_url)${path}" "$@"
  else
    curl -sS "$(_resolve_url)${path}" "$@"
  fi
}

_download_url() {
  local url="$1" output="$2"
  curl -sfS "$url" -o "$output"
}

_abs_url() {
  local url="$1"
  case "$url" in
    http://*|https://*) printf '%s' "$url" ;;
    /*) printf '%s%s' "$(_resolve_url)" "$url" ;;
    *) printf '%s' "$url" ;;
  esac
}

_api_post() {
  local path="$1" body="${2:-{\}}"
  local token
  token="$(_config_get api_token)"
  if [[ -n "$token" ]]; then
    curl -sS -X POST "$(_resolve_url)${path}" \
      -H "Authorization: Bearer $token" \
      -H "Content-Type: application/json" -d "$body"
  else
    curl -sS -X POST "$(_resolve_url)${path}" \
      -H "Content-Type: application/json" -d "$body"
  fi
}

_api_patch() {
  local path="$1" body="${2:-{\}}"
  local token
  token="$(_config_get api_token)"
  if [[ -n "$token" ]]; then
    curl -sS -X PATCH "$(_resolve_url)${path}" \
      -H "Authorization: Bearer $token" \
      -H "Content-Type: application/json" -d "$body"
  else
    curl -sS -X PATCH "$(_resolve_url)${path}" \
      -H "Content-Type: application/json" -d "$body"
  fi
}

_api_delete() {
  local path="$1" body="${2:-}"
  local token
  token="$(_config_get api_token)"
  local args=(-sS -X DELETE)
  if [[ -n "$body" ]]; then
    args+=(-H "Content-Type: application/json" -d "$body")
  fi
  if [[ -n "$token" ]]; then
    args+=(-H "Authorization: Bearer $token")
  else
    :
  fi
  curl "${args[@]}" "$(_resolve_url)${path}"
}

_api_upload_file() {
  local path="$1" file_path="$2" original_name="${3:-}"
  local token
  token="$(_config_get api_token)"
  local args=(-sS -X POST "$(_resolve_url)${path}" -F "file=@${file_path}")
  if [[ -n "$original_name" ]]; then
    args+=(-F "originalName=${original_name}")
  fi
  if [[ -n "$token" ]]; then
    args=(-H "Authorization: Bearer $token" "${args[@]}")
  fi
  curl "${args[@]}"
}

_out() {
  if $JSON_OUT; then
    cat
  elif command -v jq &>/dev/null; then
    jq .
  else
    cat
  fi
}

_green()  { printf '\033[32m%s\033[0m\n' "$*"; }
_red()    { printf '\033[31m%s\033[0m\n' "$*"; }
_bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
_dim()    { printf '\033[2m%s\033[0m\n' "$*"; }

# ── Config commands ───────────────────────────────────────────────────

cmd_config_init() {
  printf "API URL [http://localhost:8000]: "
  read -r url
  url="${url:-http://localhost:8000}"
  _config_save "{\"api_url\":\"$(_esc "$url")\",\"active_session\":\"\"}"
  _green "Config saved to $CONFIG_FILE"
}

cmd_config_set() {
  local key="${1:-}" val="${2:-}"
  if [[ -z "$key" || -z "$val" ]]; then
    echo "Usage: $CLI_NAME config set <key> <value>"; exit 1
  fi
  key="${key//-/_}"
  _config_set_key "$key" "$val"
  if [[ "$key" == "api_token" ]]; then
    _green "$key = (set)"
  else
    _green "$key = $val"
  fi
}

cmd_config_show() {
  if $JSON_OUT; then
    [[ -f "$CONFIG_FILE" ]] && cat "$CONFIG_FILE" || echo '{"api_url":"","active_session":""}'
    return
  fi
  _bold "api_url: $(_resolve_url)"
  local sess; sess=$(_config_get active_session)
  _bold "active_session: ${sess:-(not set)}"
  local token; token=$(_config_get api_token)
  _bold "api_token: $([[ -n "$token" ]] && echo "(set)" || echo "(not set)")"
  _dim "config: $CONFIG_FILE"
}

# ── Session commands ──────────────────────────────────────────────────

cmd_session_list() {
  _api_get "/api/sessions" | _out
}

cmd_session_create() {
  local name="新会话" network_egress_set=false network_egress_id="" runtime="standard_chrome"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name|-n)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME session create [--name <n>] [--network-egress <egress-id|direct>] [--runtime <standard_chrome|cloak_chromium>]"; exit 1; }
        name="$2"; shift 2 ;;
      --network-egress)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME session create [--name <n>] [--network-egress <egress-id|direct>] [--runtime <standard_chrome|cloak_chromium>]"; exit 1; }
        network_egress_set=true
        network_egress_id="$2"
        shift 2 ;;
      --runtime)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME session create [--name <n>] [--network-egress <egress-id|direct>] [--runtime <standard_chrome|cloak_chromium>]"; exit 1; }
        case "$2" in
          standard_chrome|cloak_chromium) runtime="$2" ;;
          *) echo "Invalid runtime: $2 (expected standard_chrome or cloak_chromium)"; exit 1 ;;
        esac
        shift 2 ;;
      *) echo "Unknown session create option: $1"; exit 1 ;;
    esac
  done
  local body resp
  body="{\"name\":\"$(_esc "$name")\",\"browserRuntime\":\"$runtime\""
  if $network_egress_set; then
    body="$body,\"networkEgressId\":$(_egress_json_value "$network_egress_id")"
  fi
  body="$body}"
  resp=$(_api_post "/api/sessions" "$body")
  if $JSON_OUT; then
    echo "$resp"
  else
    local sid sname
    sid=$(echo "$resp" | grep -o '"id" *: *"[^"]*"' | head -1 | sed 's/.*: *"//;s/"//')
    sname=$(echo "$resp" | grep -o '"name" *: *"[^"]*"' | head -1 | sed 's/.*: *"//;s/"//')
    _green "Created session: $sid  ($sname)"
    _dim "Copy the full id exactly. New sessions usually use 12-character ids; existing sessions may be UUIDs."
    _dim "Run: $CLI_NAME session use $sid"
  fi
}

cmd_session_set_network() {
  local egress_id="${1:-}"
  [[ -n "$egress_id" ]] || { echo "Usage: $CLI_NAME session set-network <egress-id|direct>"; exit 1; }
  shift || true
  [[ $# -eq 0 ]] || { echo "Unknown session set-network option: $1"; exit 1; }
  local resp
  resp=$(_api_post "/api/sessions/$(_sid)/network-egress" "{\"networkEgressId\":$(_egress_json_value "$egress_id")}")
  _print_or_fail_ok "$resp"
}

cmd_session_use() {
  local sid="${1:-}"
  [[ -n "$sid" ]] || { echo "Usage: $CLI_NAME session use <session-id>"; exit 1; }
  _config_set_key "active_session" "$sid"
  if $JSON_OUT; then
    echo "{\"ok\":true,\"active_session\":\"$sid\"}"
  else
    _green "Active session set to: $sid"
  fi
}

cmd_session_start() {
  local sid="${1:-$(_sid)}"
  _api_post "/api/sessions/$sid/container/start" | _out
}

cmd_session_stop() {
  local sid="${1:-$(_sid)}"
  _api_post "/api/sessions/$sid/container/stop" | _out
}

cmd_session_pause() {
  local sid="${1:-$(_sid)}"
  _api_post "/api/sessions/$sid/container/pause" | _out
}

cmd_session_unpause() {
  local sid="${1:-$(_sid)}"
  _api_post "/api/sessions/$sid/container/unpause" | _out
}

cmd_session_delete() {
  local sid="" delete_files=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --delete-files) delete_files=true; shift ;;
      *) sid="${sid:-$1}"; shift ;;
    esac
  done
  sid="${sid:-${SESSION_OPT:-}}"
  [[ -n "$sid" ]] || { echo "Usage: $CLI_NAME session delete <session-id> [--delete-files]"; exit 1; }
  if $delete_files; then
    _api_delete "/api/sessions/$sid" '{"fileDeleteMode":"all"}' | _out
  else
    _api_delete "/api/sessions/$sid" | _out
  fi
}

# ── Browser commands ──────────────────────────────────────────────────

cmd_navigate() {
  local url="${1:-}"
  [[ -n "$url" ]] || { echo "Usage: $CLI_NAME navigate <url>"; exit 1; }
  _api_post "/api/browser/navigate" \
    "{\"sessionId\":\"$(_sid)\",\"url\":\"$(_esc "$url")\"}" | _out
}

cmd_observe() {
  local mode="dom" max_candidates="40" threshold="0.05" include_screenshot="false" include_annotated="false"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode) mode="${2:-dom}"; shift 2 ;;
      --max-candidates) max_candidates="${2:-40}"; shift 2 ;;
      --threshold) threshold="${2:-0.05}"; shift 2 ;;
      --include-screenshot) include_screenshot="true"; shift ;;
      --include-annotated-screenshot) include_annotated="true"; shift ;;
      *) echo "Unknown observe option: $1"; exit 1 ;;
    esac
  done
  _api_post "/api/browser/observe" \
    "{\"sessionId\":\"$(_sid)\",\"mode\":\"$(_esc "$mode")\",\"maxCandidates\":$max_candidates,\"threshold\":$threshold,\"includeScreenshot\":$include_screenshot,\"includeAnnotatedScreenshot\":$include_annotated}" | _out
}

cmd_click() {
  local x="${1:-}" y="${2:-}"
  [[ -n "$x" && -n "$y" ]] || { echo "Usage: $CLI_NAME click <x> <y>"; exit 1; }
  _api_post "/api/browser/click" \
    "{\"sessionId\":\"$(_sid)\",\"x\":$x,\"y\":$y}" | _out
}

cmd_click_element() {
  local sel="${1:-}"
  [[ -n "$sel" ]] || { echo "Usage: $CLI_NAME click-element <css-selector>"; exit 1; }
  _api_post "/api/browser/click-element" \
    "{\"sessionId\":\"$(_sid)\",\"selector\":\"$(_esc "$sel")\"}" | _out
}

cmd_type_text() {
  local text="${1:-}"
  [[ -n "$text" ]] || { echo "Usage: $CLI_NAME type <text>"; exit 1; }
  _api_post "/api/browser/type" \
    "{\"sessionId\":\"$(_sid)\",\"text\":\"$(_esc "$text")\"}" | _out
}

cmd_key() {
  local k="${1:-}"
  [[ -n "$k" ]] || { echo "Usage: $CLI_NAME key <key-name>"; exit 1; }
  _api_post "/api/browser/key" \
    "{\"sessionId\":\"$(_sid)\",\"key\":\"$(_esc "$k")\"}" | _out
}

cmd_scroll() {
  local delta_y="${1:-}"
  [[ -n "$delta_y" ]] || { echo "Usage: $CLI_NAME scroll <delta_y>"; exit 1; }
  shift
  local delta_x=0 sx=640 sy=360
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --delta-x) delta_x="$2"; shift 2 ;;
      --x)       sx="$2"; shift 2 ;;
      --y)       sy="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  _api_post "/api/browser/scroll" \
    "{\"sessionId\":\"$(_sid)\",\"deltaY\":$delta_y,\"deltaX\":$delta_x,\"x\":$sx,\"y\":$sy}" | _out
}

cmd_tabs() {
  _api_get "/api/browser/tabs?sessionId=$(_sid)" | _out
}

cmd_switch_tab() {
  local handle="" index="" close_current=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --handle)        handle="$2"; shift 2 ;;
      --index)         index="$2"; shift 2 ;;
      --close-current) close_current=true; shift ;;
      *) shift ;;
    esac
  done
  local body="{\"sessionId\":\"$(_sid)\",\"closeCurrent\":$close_current"
  [[ -n "$handle" ]] && body="$body,\"handle\":\"$(_esc "$handle")\""
  [[ -n "$index" ]]  && body="$body,\"index\":$index"
  body="$body}"
  _api_post "/api/browser/switch-tab" "$body" | _out
}

cmd_page_info() {
  _api_get "/api/browser/current?sessionId=$(_sid)" | _out
}

cmd_screenshot() {
  local output=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --output|-o) output="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  local resp
  resp=$(_api_get "/api/browser/screenshot?sessionId=$(_sid)&includeBase64=false")
  if [[ -n "$output" ]]; then
    local file_url
    file_url=$(echo "$resp" | grep -o '"url" *: *"[^"]*"' | head -1 | sed 's/.*: *"//;s/"//')
    if [[ -z "$file_url" ]]; then
      echo "$resp" | _out
      exit 1
    fi
    _download_url "$(_abs_url "$file_url")" "$output"
    local sz; sz=$(wc -c < "$output" | tr -d ' ')
    if $JSON_OUT; then
      echo "$resp" | sed "s|}$|,\"localCopy\":\"$(_esc "$output")\",\"size\":$sz}|"
    else
      _green "Screenshot stored in FileStore and exported to $output ($sz bytes)"
    fi
  else
    echo "$resp" | _out
  fi
}

cmd_logs() {
  local tail=200
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tail|-n) tail="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  _api_get "/api/sessions/$(_sid)/logs?tail=$tail" | _out
}

cmd_files_list() {
  _api_get "/api/sessions/$(_sid)/files" | _out
}

cmd_files_upload() {
  local file_path="" original_name=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name) original_name="$2"; shift 2 ;;
      *) file_path="$1"; shift ;;
    esac
  done
  [[ -n "$file_path" ]] || { echo "Usage: $CLI_NAME files upload <path> [--name <name>]"; exit 1; }
  [[ -f "$file_path" ]] || { echo "Error: file not found: $file_path" >&2; exit 1; }
  _api_upload_file "/api/sessions/$(_sid)/files" "$file_path" "$original_name" | _out
}

cmd_files_get() {
  local file_id="${1:-}" output=""
  [[ -n "$file_id" ]] || { echo "Usage: $CLI_NAME files get <file-id> -o <path>"; exit 1; }
  shift || true
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -o|--output) output="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  [[ -n "$output" ]] || { echo "Usage: $CLI_NAME files get <file-id> -o <path>"; exit 1; }
  local resp file_url
  resp=$(_api_get "/api/sessions/$(_sid)/files/$file_id")
  file_url=$(echo "$resp" | grep -o '"url" *: *"[^"]*"' | head -1 | sed 's/.*: *"//;s/"//')
  [[ -n "$file_url" ]] || { echo "$resp" | _out; exit 1; }
  _download_url "$(_abs_url "$file_url")" "$output"
  local sz; sz=$(wc -c < "$output" | tr -d ' ')
  if $JSON_OUT; then
    echo "$resp" | sed "s|}$|,\"localCopy\":\"$(_esc "$output")\",\"size\":$sz}|"
  else
    _green "File saved to $output ($sz bytes)"
  fi
}

cmd_files_rename() {
  local file_id="${1:-}" name="${2:-}"
  [[ -n "$file_id" && -n "$name" ]] || { echo "Usage: $CLI_NAME files rename <file-id> <name>"; exit 1; }
  _api_patch "/api/sessions/$(_sid)/files/$file_id" "{\"name\":\"$(_esc "$name")\"}" | _out
}

cmd_files_delete() {
  local file_id="${1:-}"
  [[ -n "$file_id" ]] || { echo "Usage: $CLI_NAME files delete <file-id>"; exit 1; }
  _api_delete "/api/sessions/$(_sid)/files/$file_id" | _out
}

# ── Agent Device commands ───────────────────────────────────────────────

_lease_body() {
  local mode="session_bound" task_id="" ttl="" expires_at=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode)
        mode="${2:-session_bound}"; shift 2 ;;
      --task-id)
        task_id="${2:-}"; shift 2 ;;
      --ttl|--ttl-seconds)
        ttl="${2:-}"; shift 2 ;;
      --expires-at)
        expires_at="${2:-}"; shift 2 ;;
      *) echo "Unknown lease option: $1"; exit 1 ;;
    esac
  done
  if [[ -n "$ttl" ]]; then
    if ! [[ "$ttl" =~ ^[0-9]+$ ]] || (( ttl < 1 || ttl > LEASE_MAX_TTL_SECONDS )); then
      echo "Error: --ttl must be between 1 and ${LEASE_MAX_TTL_SECONDS} seconds" >&2
      exit 1
    fi
  fi
  local body="{\"leaseMode\":\"$(_esc "$mode")\""
  [[ -n "$task_id" ]] && body="$body,\"taskId\":\"$(_esc "$task_id")\""
  [[ -n "$ttl" ]] && body="$body,\"ttlSeconds\":$ttl"
  [[ -n "$expires_at" ]] && body="$body,\"expiresAt\":\"$(_esc "$expires_at")\""
  body="$body}"
  printf '%s' "$body"
}

cmd_devices() {
  _api_get "/api/agent-devices" | _out
}

cmd_device() {
  local device_id="${1:-}"
  [[ -n "$device_id" ]] || { echo "Usage: $CLI_NAME device <device-id>"; exit 1; }
  _api_get "/api/agent-devices/$device_id" | _out
}

cmd_lease_acquire() {
  local device_id="${1:-}"
  [[ -n "$device_id" ]] || { echo "Usage: $CLI_NAME lease acquire <device-id> [--mode session_bound|task_bound] [--task-id ID] [--ttl 1-${LEASE_MAX_TTL_SECONDS}|--expires-at ISO8601]"; exit 1; }
  shift || true
  _api_post "/api/agent-devices/$device_id/leases" "$(_lease_body "$@")" | _out
}

cmd_lease_renew() {
  local device_id="${1:-}" lease_id="${2:-}"
  [[ -n "$device_id" && -n "$lease_id" ]] || { echo "Usage: $CLI_NAME lease renew <device-id> <lease-id> [--ttl 1-${LEASE_MAX_TTL_SECONDS}|--expires-at ISO8601]"; exit 1; }
  shift 2 || true
  _api_patch "/api/agent-devices/$device_id/leases/$lease_id" "$(_lease_body "$@")" | _out
}

cmd_lease_release() {
  local device_id="${1:-}" lease_id="${2:-}"
  [[ -n "$device_id" && -n "$lease_id" ]] || { echo "Usage: $CLI_NAME lease release <device-id> <lease-id>"; exit 1; }
  _api_post "/api/agent-devices/$device_id/leases/$lease_id/release" "{}" | _out
}

cmd_lease_reclaim() {
  local device_id="${1:-}"
  [[ -n "$device_id" ]] || { echo "Usage: $CLI_NAME lease reclaim <device-id> [--ttl 1-${LEASE_MAX_TTL_SECONDS}|--expires-at ISO8601]"; exit 1; }
  shift || true
  _api_post "/api/agent-devices/$device_id/reclaim" "$(_lease_body "$@")" | _out
}

cmd_audit() {
  local device_id="" limit=100
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --device) device_id="${2:-}"; shift 2 ;;
      --limit|-n) limit="${2:-100}"; shift 2 ;;
      *) echo "Unknown audit option: $1"; exit 1 ;;
    esac
  done
  if [[ -n "$device_id" ]]; then
    _api_get "/api/agent-devices/$device_id/audit?limit=$limit" | _out
  else
    _api_get "/api/agent-devices/audit?limit=$limit" | _out
  fi
}

# ── Network egress commands ──────────────────────────────────────────────

cmd_network_egress_list() {
  _api_get "/api/network-egress" | _out
}

cmd_network_egress_create() {
  local name="" egress_type="" config_file="" config_url="" username="" password="" disabled=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress create --name NAME --type clash|openvpn (--config-file PATH | --config-url URL)"; exit 1; }
        name="$2"; shift 2 ;;
      --type)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress create --name NAME --type clash|openvpn (--config-file PATH | --config-url URL)"; exit 1; }
        egress_type="$2"; shift 2 ;;
      --config-file)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress create --name NAME --type clash|openvpn (--config-file PATH | --config-url URL)"; exit 1; }
        config_file="$2"; shift 2 ;;
      --config-url)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress create --name NAME --type clash|openvpn (--config-file PATH | --config-url URL)"; exit 1; }
        config_url="$2"; shift 2 ;;
      --username)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress create --name NAME --type clash|openvpn (--config-file PATH | --config-url URL)"; exit 1; }
        username="$2"; shift 2 ;;
      --password)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress create --name NAME --type clash|openvpn (--config-file PATH | --config-url URL)"; exit 1; }
        password="$2"; shift 2 ;;
      --disabled) disabled=true; shift ;;
      *) echo "Unknown network-egress create option: $1"; exit 1 ;;
    esac
  done
  [[ -n "$name" && -n "$egress_type" ]] || { echo "Usage: $CLI_NAME network-egress create --name NAME --type clash|openvpn (--config-file PATH | --config-url URL)"; exit 1; }
  case "$egress_type" in clash|openvpn) ;; *) echo "Error: --type must be clash or openvpn"; exit 1 ;; esac
  [[ -n "$config_file" || -n "$config_url" ]] || { echo "Error: pass --config-file or --config-url"; exit 1; }
  [[ -z "$config_file" || -z "$config_url" ]] || { echo "Error: --config-file and --config-url are mutually exclusive"; exit 1; }

  local body config_text
  body="{\"name\":\"$(_esc "$name")\",\"type\":\"$(_esc "$egress_type")\",\"disabled\":$disabled"
  if [[ -n "$config_file" ]]; then
    [[ -f "$config_file" ]] || { echo "Error: config file not found: $config_file" >&2; exit 1; }
    config_text=$(cat "$config_file")
    body="$body,\"configText\":\"$(_esc "$config_text")\""
  else
    body="$body,\"configUrl\":\"$(_esc "$config_url")\""
  fi
  [[ -n "$username" ]] && body="$body,\"username\":\"$(_esc "$username")\""
  [[ -n "$password" ]] && body="$body,\"password\":\"$(_esc "$password")\""
  body="$body}"
  _api_post "/api/network-egress" "$body" | _out
}

cmd_network_egress_update() {
  local egress_id="${1:-}"
  [[ -n "$egress_id" ]] || { echo "Usage: $CLI_NAME network-egress update <egress-id> [options]"; exit 1; }
  shift || true
  local name="" config_file="" config_url="" username="" password="" disabled_value="" changed=false
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress update <egress-id> [options]"; exit 1; }
        name="$2"; changed=true; shift 2 ;;
      --config-file)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress update <egress-id> [options]"; exit 1; }
        config_file="$2"; changed=true; shift 2 ;;
      --config-url)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress update <egress-id> [options]"; exit 1; }
        config_url="$2"; changed=true; shift 2 ;;
      --username)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress update <egress-id> [options]"; exit 1; }
        username="$2"; changed=true; shift 2 ;;
      --password)
        [[ -n "${2:-}" ]] || { echo "Usage: $CLI_NAME network-egress update <egress-id> [options]"; exit 1; }
        password="$2"; changed=true; shift 2 ;;
      --enable)
        [[ "$disabled_value" != "true" ]] || { echo "Error: --enable and --disable are mutually exclusive"; exit 1; }
        disabled_value=false; changed=true; shift ;;
      --disable)
        [[ "$disabled_value" != "false" ]] || { echo "Error: --enable and --disable are mutually exclusive"; exit 1; }
        disabled_value=true; changed=true; shift ;;
      *) echo "Unknown network-egress update option: $1"; exit 1 ;;
    esac
  done
  $changed || { echo "Usage: $CLI_NAME network-egress update <egress-id> [--name NAME] [--config-file PATH | --config-url URL] [--username USER --password PASS] [--enable|--disable]"; exit 1; }
  [[ -z "$config_file" || -z "$config_url" ]] || { echo "Error: --config-file and --config-url are mutually exclusive"; exit 1; }

  local body="{" sep="" config_text
  if [[ -n "$name" ]]; then
    body="$body\"name\":\"$(_esc "$name")\""; sep=","
  fi
  if [[ -n "$config_file" ]]; then
    [[ -f "$config_file" ]] || { echo "Error: config file not found: $config_file" >&2; exit 1; }
    config_text=$(cat "$config_file")
    body="$body$sep\"configText\":\"$(_esc "$config_text")\""; sep=","
  elif [[ -n "$config_url" ]]; then
    body="$body$sep\"configUrl\":\"$(_esc "$config_url")\""; sep=","
  fi
  if [[ -n "$username" ]]; then
    body="$body$sep\"username\":\"$(_esc "$username")\""; sep=","
  fi
  if [[ -n "$password" ]]; then
    body="$body$sep\"password\":\"$(_esc "$password")\""; sep=","
  fi
  if [[ -n "$disabled_value" ]]; then
    body="$body$sep\"disabled\":$disabled_value"
  fi
  body="$body}"
  _api_patch "/api/network-egress/$egress_id" "$body" | _out
}

cmd_network_egress_delete() {
  local egress_id="${1:-}"
  [[ -n "$egress_id" ]] || { echo "Usage: $CLI_NAME network-egress delete <egress-id>"; exit 1; }
  shift || true
  [[ $# -eq 0 ]] || { echo "Unknown network-egress delete option: $1"; exit 1; }
  _api_delete "/api/network-egress/$egress_id" | _out
}

cmd_network_egress_check() {
  local egress_id="${1:-}"
  [[ -n "$egress_id" ]] || { echo "Usage: $CLI_NAME network-egress check <egress-id>"; exit 1; }
  [[ "$egress_id" != "direct" ]] || { echo "Error: direct is not a managed network egress profile and cannot be checked"; exit 1; }
  shift || true
  [[ $# -eq 0 ]] || { echo "Unknown network-egress check option: $1"; exit 1; }
  _api_post "/api/network-egress/$egress_id/check" | _out
}

# ── Option parser ─────────────────────────────────────────────────────

_parse_globals() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json|-j)    JSON_OUT=true; shift ;;
      --api-url)    API_URL_OPT="$2"; shift 2 ;;
      --session|-s) SESSION_OPT="$2"; shift 2 ;;
      *)            ARGS+=("$1"); shift ;;
    esac
  done
}

# ── Main ──────────────────────────────────────────────────────────────

_load_config

ARGS=()
_parse_globals "$@"
set -- "${ARGS[@]+"${ARGS[@]}"}"

case "${1:-}" in
  config)
    shift
    case "${1:-}" in
      init)  cmd_config_init ;;
      set)   shift; cmd_config_set "$@" ;;
      show)  cmd_config_show ;;
      *)     echo "Usage: $CLI_NAME config {init|set|show}" ;;
    esac
    ;;
  session|s)
    shift
    case "${1:-}" in
      list|ls)  cmd_session_list ;;
      create)   shift; cmd_session_create "$@" ;;
      use)      shift; cmd_session_use "$@" ;;
      start)    shift; cmd_session_start "$@" ;;
      stop)     shift; cmd_session_stop "$@" ;;
      pause)    shift; cmd_session_pause "$@" ;;
      unpause|resume) shift; cmd_session_unpause "$@" ;;
      set-network) shift; cmd_session_set_network "$@" ;;
      delete)   shift; cmd_session_delete "$@" ;;
      *)        echo "Usage: $CLI_NAME session {list|create|use|start|stop|pause|unpause|set-network|delete}" ;;
    esac
    ;;
  network-egress|network)
    shift
    case "${1:-}" in
      list|ls)  shift; cmd_network_egress_list "$@" ;;
      create)   shift; cmd_network_egress_create "$@" ;;
      update)   shift; cmd_network_egress_update "$@" ;;
      delete|rm) shift; cmd_network_egress_delete "$@" ;;
      check)    shift; cmd_network_egress_check "$@" ;;
      *)        echo "Usage: $CLI_NAME network-egress {list|create|update|delete|check}" ;;
    esac
    ;;
  devices)
    shift; cmd_devices "$@" ;;
  device)
    shift; cmd_device "$@" ;;
  lease)
    shift
    case "${1:-}" in
      acquire) shift; cmd_lease_acquire "$@" ;;
      renew)   shift; cmd_lease_renew "$@" ;;
      release) shift; cmd_lease_release "$@" ;;
      reclaim) shift; cmd_lease_reclaim "$@" ;;
      *)       echo "Usage: $CLI_NAME lease {acquire|renew|release|reclaim}" ;;
    esac
    ;;
  audit)
    shift; cmd_audit "$@" ;;
  navigate)     shift; cmd_navigate "$@" ;;
  observe)      cmd_observe ;;
  click)        shift; cmd_click "$@" ;;
  click-element) shift; cmd_click_element "$@" ;;
  type)         shift; cmd_type_text "$@" ;;
  key)          shift; cmd_key "$@" ;;
  scroll)       shift; cmd_scroll "$@" ;;
  tabs)         cmd_tabs ;;
  switch-tab)   shift; cmd_switch_tab "$@" ;;
  page-info)    cmd_page_info ;;
  screenshot)   shift; cmd_screenshot "$@" ;;
  logs)         shift; cmd_logs "$@" ;;
  files)
    shift
    case "${1:-}" in
      list|ls)   shift; cmd_files_list "$@" ;;
      upload)    shift; cmd_files_upload "$@" ;;
      get)       shift; cmd_files_get "$@" ;;
      rename)    shift; cmd_files_rename "$@" ;;
      delete|rm) shift; cmd_files_delete "$@" ;;
      *)         echo "Usage: $CLI_NAME files {list|upload|get|rename|delete}" ;;
    esac
    ;;
  version|--version|-v)
    echo "$CLI_NAME $VERSION (shell)"
    ;;
  help|--help|-h|"")
    cat <<HELP
$CLI_NAME $VERSION — Remote Browser CLI (shell)

Usage: $CLI_NAME <command> [options]

Config:
  config init                  Interactive setup
  config set <key> <value>     Set config value (api-url, active-session, api-token)
  config show                  Show current configuration

Environment:
  BPILOT_API_URL               Override API URL for the current shell
  BPILOT_API_TOKEN             Use API token for the current shell
  BPILOT_ACTIVE_SESSION        Override active session for the current shell

Sessions:
  session list                 List all sessions
  session create [--name <n>] [--network-egress <id|direct>] [--runtime <standard_chrome|cloak_chromium>]
                               Create a new session
  session use <id>             Set active session
  session start <id>           Start browser container
  session stop <id>            Stop browser container
  session pause <id>           Hibernate browser container
  session unpause <id>         Resume hibernated browser container
  session set-network <id|direct>
                               Switch the target session network egress
  session delete <id>          Delete session and container; completed files are kept in Files
  session delete <id> --delete-files
                               Also delete all completed files for that session

Session target:
  Commands may omit <id> after 'session use <id>', or use --session/-s.
  Copy the full id returned by the API. New sessions usually use 12-character ids; existing sessions may be UUIDs.

Network egress:
  network                       Alias for network-egress
  network-egress list          List Direct plus managed Clash/OpenVPN profiles
  network-egress create --name <n> --type clash|openvpn (--config-file <path> | --config-url <url>)
                 [--username <user> --password <pass>] [--disabled]
                               Create a managed network egress profile
  network-egress update <id> [--name <n>] [--config-file <path> | --config-url <url>]
                 [--username <user> --password <pass>] [--enable|--disable]
                               Update a managed network egress profile
  network-egress delete <id>   Delete a managed network egress profile
  network-egress check <id>    Check a managed network egress profile

Browser (require active session):
  navigate <url>               Go to URL
  observe [--mode dom|vision|mix] Get DOM elements, YOLOv8 boxes, or visual-anchor mixed candidates
          [--include-annotated-screenshot] Include a base64 boxed screenshot
  click <x> <y>                Click at coordinates
  click-element <selector>     Click by CSS selector
  type <text>                  Type into focused input
  key <key>                    Press key (Enter, Tab, Escape, …)
  scroll <delta_y> [opts]      Scroll (--delta-x, --x, --y)
  tabs                         List browser tabs
  switch-tab [opts]            Switch tab (--handle, --index, --close-current)
  page-info                    Current URL and title
  screenshot [-o file]         Store screenshot and signed file URL; -o exports a local copy
  logs [--tail <n>]            View CDP event logs

Files:
  files list                   List session files with status
  files upload <path> [--name <n>] Upload a local file into the session
  files get <id> -o <path>     Save a completed session file locally
  files rename <id> <name>     Rename a completed session file
  files delete <id>            Delete a completed session file

Agent Devices:
  Browser Pilot exposes Session as Device and strictly supports Level 1 Device Governance only.
  Level 2 control transfer, request_intervention, handoff, and human takeover are not supported.
  devices                      List governed Agent Device sessions
  device <device-id>           Show DeviceVisibility for one session/device
  lease acquire <device-id> [--mode session_bound|task_bound] [--task-id ID] [--ttl 1-1800|--expires-at ISO8601]
                               Acquire an exclusive DeviceLease; default/max TTL is 1800s
  lease renew <device-id> <lease-id> [--ttl 1-1800|--expires-at ISO8601]
                               Update lease expiration; repeat this command to extend
  lease release <device-id> <lease-id>
                               Release the current lease
  lease reclaim <device-id> [--ttl 1-1800|--expires-at ISO8601]
                               Force reclaim and create a new lease; default/max TTL is 1800s
  audit [--device <device-id>] [--limit N]
                               List Agent Device audit events

Global options:
  --json, -j                   Machine-readable JSON output
  --api-url <url>              Override API base URL
  --session, -s <id>           Override active session ID
  --version, -v                Show version
  --help, -h                   Show this help

API: $(_resolve_url)
HELP
    ;;
  *)
    _red "Unknown command: $1"
    echo "Run '$CLI_NAME help' for usage."
    exit 1
    ;;
esac
