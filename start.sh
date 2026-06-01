#!/usr/bin/env bash
set -euo pipefail

# 端口与 frontend/vite.config.ts 耦合，改这里必须同步改 vite.config.ts
BACKEND_PORT=8000
FRONTEND_PORT=9874

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 加载 .env（自动 export 所有变量）
set -a
source .env 2>/dev/null || true
set +a

LOG_DIR="${LOG_DIR:-./logs}"
mkdir -p "$LOG_DIR"

BACKEND_PID_FILE="$LOG_DIR/backend.pid"
FRONTEND_PID_FILE="$LOG_DIR/frontend.pid"
MONITOR_PID_FILE="$LOG_DIR/dev-monitor.pid"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
MONITOR_LOG="$LOG_DIR/dev-monitor.log"
MODE=foreground
TARGET=dev
CLI_EDITION=""
EDITION_SOURCE=""

# ------------------------------------------------------------------
usage() {
    cat <<'EOF'
用法:
  ./start.sh [dev] [ce|ee]       本地开发模式（默认，Ctrl+C 停止）
  ./start.sh [dev] [ce|ee] -d    本地开发后台 daemon 模式
  ./start.sh prod [ce|ee]        生产 Docker Compose 模式（跟随日志）
  ./start.sh prod [ce|ee] -d     生产 Docker Compose 后台模式
  ./start.sh [ce|ee]            前台 watch 模式（Ctrl+C 停止）
  ./start.sh [ce|ee] -d         后台 daemon 模式
  ./start.sh --edition ce|-e ce 指定 CE 版
  ./start.sh --edition ee|-e ee 指定 EE 版
  ./start.sh stop               停止后台进程
  ./start.sh status             查看进程状态

说明:
  dev 使用本机后端/前端进程，浏览器 runtime 使用 published 模式且只绑定 127.0.0.1。
  prod 使用 docker-compose.prod.yml，公网只暴露反向代理 80/443。
  传 ce|ee 时强制指定版本。
  不传 ce|ee 时检查 ee/backend/__init__.py 和 ee/frontend/index.ts；都有才按 EE，否则按 CE。
EOF
}

die_usage() {
    echo "$1" >&2
    usage >&2
    exit 1
}

normalize_edition() {
    local value normalized
    value="${1:-}"
    normalized=$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')
    case "$normalized" in
        ce|ee) printf '%s' "$normalized" ;;
        *) return 1 ;;
    esac
}

set_cli_edition() {
    local normalized
    normalized=$(normalize_edition "$1") || die_usage "无效 edition: $1（只能是 ce 或 ee）"
    if [[ -n "$CLI_EDITION" && "$CLI_EDITION" != "$normalized" ]]; then
        die_usage "edition 只能指定一次"
    fi
    CLI_EDITION="$normalized"
}

set_mode() {
    local mode="$1"
    if [[ "$MODE" != "foreground" && "$MODE" != "$mode" ]]; then
        die_usage "启动模式只能指定一个"
    fi
    MODE="$mode"
}

set_target() {
    local target="$1"
    if [[ "$TARGET" != "dev" && "$TARGET" != "$target" ]]; then
        die_usage "启动目标只能指定一个"
    fi
    TARGET="$target"
}

resolve_edition() {
    if [[ -n "$CLI_EDITION" ]]; then
        export EDITION="$CLI_EDITION"
        EDITION_SOURCE="参数"
        return
    fi

    if [[ -f "$SCRIPT_DIR/ee/backend/__init__.py" && -f "$SCRIPT_DIR/ee/frontend/index.ts" ]]; then
        export EDITION=ee
        EDITION_SOURCE="ee目录探测"
        return
    fi

    export EDITION=ce
    EDITION_SOURCE="ee目录探测"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            ce|CE|ee|EE)
                set_cli_edition "$1"
                shift
                ;;
            dev)
                set_target dev
                shift
                ;;
            prod)
                set_target prod
                shift
                ;;
            --edition)
                [[ $# -ge 2 ]] || die_usage "--edition 需要指定 ce 或 ee"
                set_cli_edition "$2"
                shift 2
                ;;
            --edition=*)
                set_cli_edition "${1#--edition=}"
                shift
                ;;
            -e)
                [[ $# -ge 2 ]] || die_usage "-e 需要指定 ce 或 ee"
                set_cli_edition "$2"
                shift 2
                ;;
            -d|--daemon)
                set_mode daemon
                shift
                ;;
            stop|status)
                set_mode "$1"
                shift
                ;;
            -h|--help)
                set_mode help
                shift
                ;;
            *)
                die_usage "未知参数: $1"
                ;;
        esac
    done
    case "$MODE" in
        foreground|daemon) resolve_edition ;;
    esac
}

parse_args "$@"

# ------------------------------------------------------------------
_is_running() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

_kill_process_tree() {
    local pid="${1:-}" child
    if [[ -z "$pid" ]]; then
        return
    fi
    if command -v pgrep >/dev/null 2>&1; then
        while read -r child; do
            [[ -n "$child" ]] && _kill_process_tree "$child"
        done < <(pgrep -P "$pid" 2>/dev/null || true)
    fi
    kill "$pid" 2>/dev/null || true
}

_kill_pid_file() {
    local pid_file="$1" name="$2"
    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            _kill_process_tree "$pid"
            echo "[$name] 已停止 (PID $pid)"
        else
            echo "[$name] 进程不存在 (PID $pid)"
        fi
        rm -f "$pid_file"
    else
        echo "[$name] 未运行"
    fi
}

# ------------------------------------------------------------------
do_stop() {
    _kill_pid_file "$MONITOR_PID_FILE" "monitor"
    _kill_pid_file "$BACKEND_PID_FILE" "backend"
    _kill_pid_file "$FRONTEND_PID_FILE" "frontend"
}

do_status() {
    for name in backend frontend dev-monitor; do
        local pid_file="$LOG_DIR/${name}.pid"
        if _is_running "$pid_file"; then
            echo "[$name] 运行中 (PID $(cat "$pid_file"))"
        else
            echo "[$name] 未运行"
        fi
    done
}

_compose_file() {
    if [[ "$TARGET" == "prod" ]]; then
        printf '%s\n' "docker-compose.prod.yml"
    else
        printf '%s\n' "docker-compose.yml"
    fi
}

# ------------------------------------------------------------------
_ensure_postgres() {
    echo "[postgres] 确保 PostgreSQL 运行中..."
    docker compose up -d postgres
    local tries=0
    until docker compose exec -T postgres pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [[ $tries -ge 30 ]]; then
            echo "[postgres] 等待超时，继续启动（backend 会自行重试连接）"
            break
        fi
        sleep 1
    done
    echo "[postgres] ready"
}

_ensure_object_storage() {
    echo "[s3] 确保内置 S3 兼容对象存储运行中..."
    docker compose up -d minio
    docker compose up minio-init
    echo "[s3] ready"
}

_ensure_deps() {
    if [[ ! -d backend/.venv ]]; then
        echo "[backend] 安装 Python 依赖 (uv sync)..."
        (cd backend && uv sync)
    fi
    if [[ ! -d frontend/node_modules ]]; then
        echo "[frontend] 安装 Node 依赖 (npm install)..."
        (cd frontend && npm install)
    fi
}

_port_has_listener() {
    local port="$1"
    python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
for host in ("127.0.0.1", "::1"):
    try:
        with socket.create_connection((host, port), timeout=0.2):
            sys.exit(0)
    except OSError:
        pass
sys.exit(1)
PY
}

_print_port_owner() {
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | sed -n '1,6p' >&2 || true
    fi
}

_check_port_available() {
    local name="$1" port="$2"
    if _port_has_listener "$port"; then
        echo "[$name] 端口 $port 已被占用，拒绝半启动。请先停止占用进程或执行 ./start.sh stop。" >&2
        _print_port_owner "$port"
        return 1
    fi
    return 0
}

_require_dev_ports_available() {
    local failed=0
    _check_port_available "backend" "$BACKEND_PORT" || failed=1
    _check_port_available "frontend" "$FRONTEND_PORT" || failed=1
    if (( failed )); then
        exit 1
    fi
}

_http_ok() {
    local url="$1"
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 2 "$url" >/dev/null 2>&1
    else
        python3 - "$url" <<'PY'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as response:
        sys.exit(0 if 200 <= response.status < 400 else 1)
except Exception:
    sys.exit(1)
PY
    fi
}

_print_recent_log() {
    local name="$1" log_file="$2"
    echo "[$name] 最近日志 ($log_file):" >&2
    tail -80 "$log_file" >&2 2>/dev/null || true
}

_stop_started_processes() {
    local backend_pid="${1:-}" frontend_pid="${2:-}" tail_pid="${3:-}"
    if [[ -n "$tail_pid" ]]; then
        _kill_process_tree "$tail_pid"
    fi
    if [[ -n "$backend_pid" ]]; then
        _kill_process_tree "$backend_pid"
    fi
    if [[ -n "$frontend_pid" ]]; then
        _kill_process_tree "$frontend_pid"
    fi
    if [[ -n "$tail_pid" ]]; then
        wait "$tail_pid" 2>/dev/null || true
    fi
    if [[ -n "$backend_pid" ]]; then
        wait "$backend_pid" 2>/dev/null || true
    fi
    if [[ -n "$frontend_pid" ]]; then
        wait "$frontend_pid" 2>/dev/null || true
    fi
    rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE" "$MONITOR_PID_FILE"
}

_start_pair_monitor() {
    local backend_pid="$1" frontend_pid="$2"
    : > "$MONITOR_LOG"
    (
        trap '' INT HUP
        while true; do
            if ! kill -0 "$backend_pid" 2>/dev/null; then
                echo "[monitor] backend 进程退出，停止 frontend。"
                _kill_process_tree "$frontend_pid"
                rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE" "$MONITOR_PID_FILE"
                exit 0
            fi
            if ! kill -0 "$frontend_pid" 2>/dev/null; then
                echo "[monitor] frontend 进程退出，停止 backend。"
                _kill_process_tree "$backend_pid"
                rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE" "$MONITOR_PID_FILE"
                exit 0
            fi
            sleep 1
        done
    ) >> "$MONITOR_LOG" 2>&1 &
    echo $! > "$MONITOR_PID_FILE"
}

_wait_service_ready() {
    local name="$1" pid="$2" url="$3" log_file="$4" timeout_seconds="$5"
    local deadline=$((SECONDS + timeout_seconds))
    while (( SECONDS < deadline )); do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "[$name] 启动失败，配套服务将一起停止。" >&2
            _print_recent_log "$name" "$log_file"
            return 1
        fi
        if _http_ok "$url"; then
            echo "[$name] ready ($url)"
            return 0
        fi
        sleep 1
    done

    echo "[$name] 启动超时，配套服务将一起停止: $url" >&2
    _print_recent_log "$name" "$log_file"
    return 1
}

_infer_postgres_env_from_database_url() {
    if [[ -z "${DATABASE_URL:-}" ]]; then
        return
    fi
    if [[ -n "${POSTGRES_USER:-}" && -n "${POSTGRES_PASSWORD:-}" && -n "${POSTGRES_DB:-}" ]]; then
        return
    fi

    local assignments
    assignments=$(DATABASE_URL="$DATABASE_URL" \
        POSTGRES_USER="${POSTGRES_USER:-}" \
        POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}" \
        POSTGRES_DB="${POSTGRES_DB:-}" \
        python3 - <<'PY'
import os
import shlex
from urllib.parse import unquote, urlparse

try:
    parsed = urlparse(os.environ.get("DATABASE_URL", ""))
except Exception:
    parsed = None

values = {}
if parsed:
    if parsed.username:
        values["POSTGRES_USER"] = unquote(parsed.username)
    if parsed.password is not None:
        values["POSTGRES_PASSWORD"] = unquote(parsed.password)
    db_name = (parsed.path or "").lstrip("/").split("/", 1)[0]
    if db_name:
        values["POSTGRES_DB"] = unquote(db_name)

for key, value in values.items():
    if not os.environ.get(key):
        print(f"export {key}={shlex.quote(value)}")
PY
    ) || true
    if [[ -n "$assignments" ]]; then
        eval "$assignments"
    fi
}

_ensure_local_compose_runtime_env() {
    if [[ -z "${BROWSER_RUNTIME_CONTROL_TOKEN:-}" ]]; then
        if [[ -n "${BROWSER_RUNTIME_CONTROL_URL:-}" ]]; then
            echo "缺少启动配置: BROWSER_RUNTIME_CONTROL_TOKEN" >&2
            echo "已配置 BROWSER_RUNTIME_CONTROL_URL 时必须同时设置 BROWSER_RUNTIME_CONTROL_TOKEN。" >&2
            exit 1
        fi
        export BROWSER_RUNTIME_CONTROL_TOKEN="browserpilot-local-start-runtime-token"
        echo "[runtime] 未配置 BROWSER_RUNTIME_CONTROL_TOKEN，本地 start.sh 使用临时占位值用于 Docker Compose 解析"
    fi
}

_require_database_env() {
    _infer_postgres_env_from_database_url

    local missing=()
    local key
    for key in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB DATABASE_URL MINIO_ROOT_USER MINIO_ROOT_PASSWORD MINIO_BUCKET; do
        if [[ -z "${!key:-}" ]]; then
            missing+=("$key")
        fi
    done

    if (( ${#missing[@]} > 0 )); then
        echo "缺少启动配置: ${missing[*]}" >&2
        echo "请先执行: cp .env.example .env" >&2
        echo "然后按需修改 .env 中的 DATABASE_URL、POSTGRES_*、MINIO_*（内置 S3 兼容对象存储）。" >&2
        exit 1
    fi
}

_require_prod_env() {
    _infer_postgres_env_from_database_url
    export APP_ENV="${APP_ENV:-production}"
    export NGINX_TLS_CERT_FILE="${NGINX_TLS_CERT_FILE:-fullchain.pem}"
    export NGINX_TLS_KEY_FILE="${NGINX_TLS_KEY_FILE:-privkey.pem}"

    local missing=()
    local key
    for key in NGINX_SERVER_NAME APP_PUBLIC_ORIGINS API_BASE_URL BROWSER_VNC_PASSWORD_SECRET BROWSER_RUNTIME_CONTROL_TOKEN POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB MINIO_ROOT_USER MINIO_ROOT_PASSWORD MINIO_BUCKET MINIO_PUBLIC_ENDPOINT; do
        if [[ -z "${!key:-}" ]]; then
            missing+=("$key")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        echo "缺少生产配置: ${missing[*]}" >&2
        echo "prod 模式需要 Nginx 域名和 TLS 证书、公开 Origin、VNC 密钥、runtime token、数据库和对象存储公开下载地址。" >&2
        exit 1
    fi
    local nginx_cert_dir="$SCRIPT_DIR/deploy/nginx/certs"
    if [[ ! -f "$nginx_cert_dir/$NGINX_TLS_CERT_FILE" ]]; then
        echo "缺少 Nginx TLS 证书: $nginx_cert_dir/$NGINX_TLS_CERT_FILE" >&2
        exit 1
    fi
    if [[ ! -f "$nginx_cert_dir/$NGINX_TLS_KEY_FILE" ]]; then
        echo "缺少 Nginx TLS 私钥: $nginx_cert_dir/$NGINX_TLS_KEY_FILE" >&2
        exit 1
    fi
    if [[ "${BROWSER_RUNTIME_ACCESS_MODE:-private}" == "published" ]]; then
        echo "生产模式禁止 BROWSER_RUNTIME_ACCESS_MODE=published" >&2
        exit 1
    fi
    if [[ "$APP_ENV" != "production" && "$APP_ENV" != "prod" ]]; then
        echo "生产模式要求 APP_ENV=production 或 prod" >&2
        exit 1
    fi
}

_start_processes() {
    _require_database_env
    _ensure_local_compose_runtime_env
    export BROWSER_RUNTIME_ACCESS_MODE="${BROWSER_RUNTIME_ACCESS_MODE:-published}"
    export MINIO_STORAGE_BOOTSTRAP="${MINIO_STORAGE_BOOTSTRAP:-true}"
    export MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
    echo "[edition] $EDITION ($EDITION_SOURCE)"
    _ensure_deps
    _require_dev_ports_available
    _ensure_postgres
    _ensure_object_storage

    : > "$BACKEND_LOG"
    : > "$FRONTEND_LOG"

    (cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload) >> "$BACKEND_LOG" 2>&1 &
    local backend_pid=$!
    echo $backend_pid > "$BACKEND_PID_FILE"

    (cd frontend && npm run dev -- --port "$FRONTEND_PORT") >> "$FRONTEND_LOG" 2>&1 &
    local frontend_pid=$!
    echo $frontend_pid > "$FRONTEND_PID_FILE"

    echo "[backend]  PID=$backend_pid  port=$BACKEND_PORT  log=$BACKEND_LOG"
    echo "[frontend] PID=$frontend_pid port=$FRONTEND_PORT log=$FRONTEND_LOG"

    if ! _wait_service_ready "backend" "$backend_pid" "http://127.0.0.1:$BACKEND_PORT/readyz" "$BACKEND_LOG" 90; then
        _stop_started_processes "$backend_pid" "$frontend_pid"
        exit 1
    fi
    if ! _wait_service_ready "frontend" "$frontend_pid" "http://127.0.0.1:$FRONTEND_PORT/" "$FRONTEND_LOG" 45; then
        _stop_started_processes "$backend_pid" "$frontend_pid"
        exit 1
    fi
}

do_prod() {
    _require_prod_env
    _ensure_local_compose_runtime_env
    echo "[edition] $EDITION ($EDITION_SOURCE)"
    echo "[prod] 使用 docker-compose.prod.yml 启动 Nginx 公网边界"
    docker compose -f docker-compose.prod.yml up -d
    if [[ "$MODE" == "foreground" ]]; then
        docker compose -f docker-compose.prod.yml logs -f reverse-proxy backend runtime-worker
    else
        docker compose -f docker-compose.prod.yml ps
    fi
}

do_prod_stop() {
    docker compose -f docker-compose.prod.yml down
}

do_prod_status() {
    docker compose -f docker-compose.prod.yml ps
}

# ------------------------------------------------------------------
do_foreground() {
    _start_processes

    local backend_pid frontend_pid
    backend_pid=$(cat "$BACKEND_PID_FILE")
    frontend_pid=$(cat "$FRONTEND_PID_FILE")

    sleep 0.3
    tail -f "$BACKEND_LOG" "$FRONTEND_LOG" &
    local tail_pid=$!

    cleanup() {
        local exit_code="${1:-130}"
        echo ""
        echo "正在停止..."
        _stop_started_processes "$backend_pid" "$frontend_pid" "$tail_pid"
        echo "已停止"
        exit "$exit_code"
    }
    trap 'cleanup 130' SIGINT
    trap 'cleanup 143' SIGTERM

    local stopped_name stopped_pid other_pid status
    while true; do
        if ! kill -0 "$backend_pid" 2>/dev/null; then
            stopped_name="backend"
            stopped_pid="$backend_pid"
            other_pid="$frontend_pid"
            break
        fi
        if ! kill -0 "$frontend_pid" 2>/dev/null; then
            stopped_name="frontend"
            stopped_pid="$frontend_pid"
            other_pid="$backend_pid"
            break
        fi
        sleep 1
    done

    set +e
    wait "$stopped_pid" 2>/dev/null
    status=$?
    set -e

    echo "[$stopped_name] 进程退出，停止配套服务..."
    _stop_started_processes "$backend_pid" "$frontend_pid" "$tail_pid"
    return "$status"
}

do_daemon() {
    if _is_running "$BACKEND_PID_FILE" || _is_running "$FRONTEND_PID_FILE"; then
        echo "已有后台进程运行，先执行 ./start.sh stop"
        do_status
        exit 1
    fi

    _start_processes
    _start_pair_monitor "$(cat "$BACKEND_PID_FILE")" "$(cat "$FRONTEND_PID_FILE")"
    echo ""
    echo "后台模式已启动，Ctrl+C 退出日志查看（不影响后台进程）"
    echo "停止: ./start.sh stop"
    echo ""

    tail -f "$BACKEND_LOG" "$FRONTEND_LOG" "$MONITOR_LOG"
}

# ------------------------------------------------------------------
case "$MODE" in
    stop)       if [[ "$TARGET" == "prod" ]]; then do_prod_stop; else do_stop; fi ;;
    status)     if [[ "$TARGET" == "prod" ]]; then do_prod_status; else do_status; fi ;;
    daemon)     if [[ "$TARGET" == "prod" ]]; then do_prod; else do_daemon; fi ;;
    foreground) if [[ "$TARGET" == "prod" ]]; then do_prod; else do_foreground; fi ;;
    help)       usage ;;
esac
