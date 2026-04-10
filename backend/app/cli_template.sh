#!/usr/bin/env bash
set -euo pipefail

VERSION="0.1.0"
API_URL="{{API_URL}}"
CLI_NAME="{{CLI_NAME}}"
CONFIG_DIR="${HOME}/.${CLI_NAME}"
CONFIG_FILE="${CONFIG_DIR}/config.json"

# ── Globals (set by option parser) ────────────────────────────────────
JSON_OUT=false
API_URL_OPT=""
SESSION_OPT=""

# ── Helpers ───────────────────────────────────────────────────────────

_load_config() {
  [[ -f "$CONFIG_FILE" ]] || return 0
  local v
  v=$(grep -o '"api_url" *: *"[^"]*"' "$CONFIG_FILE" 2>/dev/null | head -1 | sed 's/.*: *"//;s/"//') || true
  [[ -n "$v" ]] && API_URL="$v"
}

_config_get() {
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
  printf '%s' "$s"
}

_resolve_url() {
  local u="${API_URL_OPT:-$API_URL}"
  printf '%s' "${u%/}"
}

_sid() {
  local s="${SESSION_OPT:-$(_config_get active_session)}"
  if [[ -z "$s" ]]; then
    echo "Error: No active session. Run: $CLI_NAME session use <id>" >&2
    exit 1
  fi
  printf '%s' "$s"
}

_api_get() {
  local path="$1"; shift
  curl -sfS "$(_resolve_url)${path}" "$@"
}

_api_post() {
  local path="$1" body="${2:-{\}}"
  curl -sfS -X POST "$(_resolve_url)${path}" \
    -H "Content-Type: application/json" -d "$body"
}

_api_delete() {
  local path="$1"
  curl -sfS -X DELETE "$(_resolve_url)${path}"
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
  _green "$key = $val"
}

cmd_config_show() {
  if $JSON_OUT; then
    [[ -f "$CONFIG_FILE" ]] && cat "$CONFIG_FILE" || echo '{"api_url":"","active_session":""}'
    return
  fi
  _bold "api_url: $(_resolve_url)"
  local sess; sess=$(_config_get active_session)
  _bold "active_session: ${sess:-(not set)}"
  _dim "config: $CONFIG_FILE"
}

# ── Session commands ──────────────────────────────────────────────────

cmd_session_list() {
  _api_get "/api/sessions" | _out
}

cmd_session_create() {
  local name="新会话"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name|-n) name="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  local resp
  resp=$(_api_post "/api/sessions" "{\"name\":\"$(_esc "$name")\"}")
  if $JSON_OUT; then
    echo "$resp"
  else
    local sid sname
    sid=$(echo "$resp" | grep -o '"id" *: *"[^"]*"' | head -1 | sed 's/.*: *"//;s/"//')
    sname=$(echo "$resp" | grep -o '"name" *: *"[^"]*"' | head -1 | sed 's/.*: *"//;s/"//')
    _green "Created session: $sid  ($sname)"
    _dim "Run: $CLI_NAME session use $sid"
  fi
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

cmd_session_delete() {
  local sid="${1:-}"
  [[ -n "$sid" ]] || { echo "Usage: $CLI_NAME session delete <session-id>"; exit 1; }
  _api_delete "/api/sessions/$sid" | _out
}

# ── Browser commands ──────────────────────────────────────────────────

cmd_navigate() {
  local url="${1:-}"
  [[ -n "$url" ]] || { echo "Usage: $CLI_NAME navigate <url>"; exit 1; }
  _api_post "/api/browser/navigate" \
    "{\"sessionId\":\"$(_sid)\",\"url\":\"$(_esc "$url")\"}" | _out
}

cmd_observe() {
  _api_post "/api/browser/observe" "{\"sessionId\":\"$(_sid)\"}" | _out
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
  resp=$(_api_get "/api/browser/screenshot?sessionId=$(_sid)")
  if [[ -n "$output" ]]; then
    local b64
    b64=$(echo "$resp" | grep -o '"screenshot" *: *"[^"]*"' | sed 's/.*: *"//;s/"//')
    if [[ "$(uname)" == "Darwin" ]]; then
      echo "$b64" | base64 -D > "$output"
    else
      echo "$b64" | base64 -d > "$output"
    fi
    local sz; sz=$(wc -c < "$output" | tr -d ' ')
    if $JSON_OUT; then
      echo "{\"ok\":true,\"file\":\"$output\",\"size\":$sz}"
    else
      _green "Screenshot saved to $output ($sz bytes)"
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
      delete)   shift; cmd_session_delete "$@" ;;
      *)        echo "Usage: $CLI_NAME session {list|create|use|start|stop|delete}" ;;
    esac
    ;;
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
  version|--version|-v)
    echo "$CLI_NAME $VERSION (shell)"
    ;;
  help|--help|-h|"")
    cat <<HELP
$CLI_NAME $VERSION — Remote Browser CLI (shell)

Usage: $CLI_NAME <command> [options]

Config:
  config init                  Interactive setup
  config set <key> <value>     Set config value (api-url, active-session)
  config show                  Show current configuration

Sessions:
  session list                 List all sessions
  session create [--name <n>]  Create a new session
  session use <id>             Set active session
  session start [id]           Start browser container
  session stop [id]            Stop browser container
  session delete <id>          Delete session and container

Browser (require active session):
  navigate <url>               Go to URL
  observe                      Get page elements with coordinates
  click <x> <y>               Click at coordinates
  click-element <selector>     Click by CSS selector
  type <text>                  Type into focused input
  key <key>                    Press key (Enter, Tab, Escape, …)
  scroll <delta_y> [opts]      Scroll (--delta-x, --x, --y)
  tabs                         List browser tabs
  switch-tab [opts]            Switch tab (--handle, --index, --close-current)
  page-info                    Current URL and title
  screenshot [-o file]         Take screenshot
  logs [--tail <n>]            View CDP event logs

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
