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
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

# ------------------------------------------------------------------
usage() {
    cat <<'EOF'
用法:
  ./start.sh           前台 watch 模式（Ctrl+C 停止）
  ./start.sh -d        后台 daemon 模式
  ./start.sh stop      停止后台进程
  ./start.sh status    查看进程状态
EOF
}

# ------------------------------------------------------------------
_is_running() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

_kill_pid_file() {
    local pid_file="$1" name="$2"
    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
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
    _kill_pid_file "$BACKEND_PID_FILE" "backend"
    _kill_pid_file "$FRONTEND_PID_FILE" "frontend"
}

do_status() {
    for name in backend frontend; do
        local pid_file="$LOG_DIR/${name}.pid"
        if _is_running "$pid_file"; then
            echo "[$name] 运行中 (PID $(cat "$pid_file"))"
        else
            echo "[$name] 未运行"
        fi
    done
}

# ------------------------------------------------------------------
_ensure_postgres() {
    echo "[postgres] 确保 PostgreSQL 运行中..."
    docker compose up -d postgres
    local tries=0
    until docker compose exec -T postgres pg_isready -U ${POSTGRES_USER:-nodeskpane} >/dev/null 2>&1; do
        tries=$((tries + 1))
        if [[ $tries -ge 30 ]]; then
            echo "[postgres] 等待超时，继续启动（backend 会自行重试连接）"
            break
        fi
        sleep 1
    done
    echo "[postgres] ready"
}

_start_processes() {
    _ensure_postgres

    echo "[selenium] 构建 Selenium 镜像..."
    docker compose build selenium

    : > "$BACKEND_LOG"
    : > "$FRONTEND_LOG"

    (cd backend && uvicorn app.main:app --host 0.0.0.0 --port "$BACKEND_PORT" --reload) >> "$BACKEND_LOG" 2>&1 &
    local backend_pid=$!
    echo $backend_pid > "$BACKEND_PID_FILE"

    (cd frontend && npm run dev -- --port "$FRONTEND_PORT") >> "$FRONTEND_LOG" 2>&1 &
    local frontend_pid=$!
    echo $frontend_pid > "$FRONTEND_PID_FILE"

    echo "[backend]  PID=$backend_pid  port=$BACKEND_PORT  log=$BACKEND_LOG"
    echo "[frontend] PID=$frontend_pid port=$FRONTEND_PORT log=$FRONTEND_LOG"
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
        echo ""
        echo "正在停止..."
        kill "$tail_pid" 2>/dev/null || true
        kill "$backend_pid" 2>/dev/null || true
        kill "$frontend_pid" 2>/dev/null || true
        wait "$tail_pid" 2>/dev/null || true
        wait "$backend_pid" 2>/dev/null || true
        wait "$frontend_pid" 2>/dev/null || true
        rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE"
        echo "已停止"
    }
    trap cleanup SIGINT SIGTERM

    wait "$backend_pid" "$frontend_pid" 2>/dev/null || true
    kill "$tail_pid" 2>/dev/null || true
    rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE"
}

do_daemon() {
    if _is_running "$BACKEND_PID_FILE" || _is_running "$FRONTEND_PID_FILE"; then
        echo "已有后台进程运行，先执行 ./start.sh stop"
        do_status
        exit 1
    fi

    _start_processes
    echo ""
    echo "后台模式已启动，Ctrl+C 退出日志查看（不影响后台进程）"
    echo "停止: ./start.sh stop"
    echo ""

    tail -f "$BACKEND_LOG" "$FRONTEND_LOG"
}

# ------------------------------------------------------------------
case "${1:-}" in
    stop)    do_stop ;;
    status)  do_status ;;
    -d)      do_daemon ;;
    "")      do_foreground ;;
    -h|--help) usage ;;
    *)       echo "未知参数: $1"; usage; exit 1 ;;
esac
