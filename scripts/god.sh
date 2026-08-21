#!/usr/bin/env bash
# GOD launcher: starts the backend (live pixel town API) and the control room.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${GOD_ENV_FILE:-$ROOT_DIR/.env}"
BACKEND_ROOT="${BACKEND_ROOT:-$ROOT_DIR/agentsociety}"

STATE_DIR="$ROOT_DIR/.god"
LOG_DIR="$STATE_DIR/logs"
PID_DIR="$STATE_DIR/pids"
TOWN_DIR="$STATE_DIR/town"
mkdir -p "$LOG_DIR" "$PID_DIR" "$TOWN_DIR"

BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"

GOD_BACKEND_HOST="${GOD_BACKEND_HOST:-127.0.0.1}"
GOD_BACKEND_PORT="${GOD_BACKEND_PORT:-8001}"
GOD_FRONTEND_PORT="${GOD_FRONTEND_PORT:-5174}"

backend_url=""
frontend_url=""

log() {
  printf '[GOD] %s\n' "$*"
}

die() {
  printf '[GOD] error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: ./scripts/god.sh [start|restart|stop|status|tail|setup|reset]

start    Start the backend and the control room, then print the URL.
restart  Stop everything cleanly, then start.
stop     Stop GOD and release its ports.
status   Print URLs, ports, and how many AI residents are configured.
tail     Follow the backend and control-room logs.
setup    Install or refresh Python and Node dependencies only.
reset    Stop, then remove every saved AI resident (.god/town/agents.json).
EOF
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

shell_quote() {
  printf '%q' "$1"
}

screen_list() {
  screen -ls 2>/dev/null || true
}

require_base_tools() {
  for tool in uv npm curl lsof python3; do
    command_exists "$tool" || die "Required command not found: $tool"
  done
}

refresh_derived() {
  backend_url="http://$GOD_BACKEND_HOST:$GOD_BACKEND_PORT"
  frontend_url="http://127.0.0.1:$GOD_FRONTEND_PORT"
}

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
  GOD_BACKEND_HOST="${GOD_BACKEND_HOST:-127.0.0.1}"
  GOD_BACKEND_PORT="${GOD_BACKEND_PORT:-8001}"
  GOD_FRONTEND_PORT="${GOD_FRONTEND_PORT:-5174}"
  refresh_derived
}

ensure_env_file() {
  if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ROOT_DIR/.env.example" "$ENV_FILE"
    log "Created .env from .env.example"
  fi
  load_env
}

is_port_open() {
  local port="$1"
  python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
for host in ("127.0.0.1", "::1", "localhost"):
    try:
        with socket.create_connection((host, port), timeout=0.35):
            raise SystemExit(0)
    except OSError:
        pass
raise SystemExit(1)
PY
}

wait_for_port() {
  local port="$1"
  local label="$2"
  local timeout="${3:-120}"
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if is_port_open "$port"; then
      log "$label ready on port $port"
      return 0
    fi
    sleep 1
  done
  log "Last 30 log lines for $label:"
  tail -n 30 "$LOG_DIR/${label// /-}.log" 2>/dev/null || true
  die "Timed out waiting for $label on port $port"
}

kill_pid_file() {
  local file="$1"
  local label="$2"
  [[ -f "$file" ]] || return 0
  local pid
  pid="$(cat "$file" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    log "Stopping $label pid=$pid"
    kill "$pid" 2>/dev/null || true
    local deadline=$((SECONDS + 8))
    while (( SECONDS < deadline )); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.3
    done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$file"
}

kill_listeners_on_port() {
  local port="$1"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
  [[ -n "${pids// }" ]] || return 0
  log "Clearing port $port"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 0.6
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ' || true)"
  if [[ -n "${pids// }" ]]; then
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

stop_known_screens() {
  command_exists screen || return 0
  for name in god-backend god-frontend god-town agentsociety-backend agentsociety-frontend; do
    if screen_list | grep -q "[.]$name[[:space:]]"; then
      log "Stopping leftover session: $name"
      screen -S "$name" -X quit >/dev/null 2>&1 || true
    fi
  done
}

start_detached_service() {
  local session_name="$1"
  local pid_file="$2"
  local command="$3"

  if command_exists screen; then
    if screen_list | grep -q "[.]$session_name[[:space:]]"; then
      log "Replacing old session: $session_name"
      screen -S "$session_name" -X quit >/dev/null 2>&1 || true
      sleep 0.5
    fi
    screen -dmS "$session_name" bash -lc "$command"
    sleep 0.5
    local screen_pid
    screen_pid="$(
      screen_list \
        | awk -v name="$session_name" '$1 ~ "[.]" name "$" { split($1, a, "."); print a[1]; exit }'
    )"
    [[ -n "$screen_pid" ]] || die "Failed to create session: $session_name"
    printf '%s\n' "$screen_pid" > "$pid_file"
  else
    nohup bash -lc "$command" >/dev/null 2>&1 &
    printf '%s\n' "$!" > "$pid_file"
  fi
}

setup_deps() {
  require_base_tools
  if [[ "${GOD_SKIP_SETUP:-0}" == "1" ]]; then
    log "Skipping dependency setup (GOD_SKIP_SETUP=1)"
    return 0
  fi

  if [[ ! -d "$BACKEND_ROOT/.venv" || "${GOD_FORCE_SETUP:-0}" == "1" ]]; then
    log "Syncing backend Python dependencies"
    (cd "$BACKEND_ROOT" && uv sync)
  fi

  if [[ ! -d "$BACKEND_ROOT/frontend/node_modules" || "${GOD_FORCE_SETUP:-0}" == "1" ]]; then
    log "Installing control-room dependencies"
    npm install --no-audit --no-fund --loglevel=error --prefix "$BACKEND_ROOT/frontend"
  fi
}

start_backend() {
  ensure_env_file
  if is_port_open "$GOD_BACKEND_PORT" && curl -fsS "$backend_url/health" >/dev/null 2>&1; then
    log "Backend already up"
    return 0
  fi

  log "Starting backend"
  : > "$LOG_DIR/Backend.log"
  local backend_log_level="${BACKEND_LOG_LEVEL:-info}"
  local backend_cmd
  backend_cmd="cd $(shell_quote "$BACKEND_ROOT")"
  backend_cmd+=" && set -a && source $(shell_quote "$ENV_FILE") && set +a"
  backend_cmd+=" && export GOD_ROOT=$(shell_quote "$ROOT_DIR")"
  backend_cmd+=" && export GOD_ENV_FILE=$(shell_quote "$ENV_FILE")"
  backend_cmd+=" && export GOD_STATE_DIR=$(shell_quote "$STATE_DIR")"
  backend_cmd+=" && export BACKEND_HOST=$(shell_quote "$GOD_BACKEND_HOST")"
  backend_cmd+=" && export BACKEND_PORT=$(shell_quote "$GOD_BACKEND_PORT")"
  backend_cmd+=" && export BACKEND_LOG_LEVEL=$(shell_quote "$backend_log_level")"
  backend_cmd+=" && exec uv run python -m agentsociety2.backend.run --log-level $(shell_quote "$backend_log_level") >> $(shell_quote "$LOG_DIR/Backend.log") 2>&1"
  start_detached_service "god-backend" "$BACKEND_PID_FILE" "$backend_cmd"

  wait_for_port "$GOD_BACKEND_PORT" "Backend" 120
  local deadline=$((SECONDS + 30))
  while (( SECONDS < deadline )); do
    curl -fsS "$backend_url/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  die "Backend port is open but /health did not respond"
}

start_frontend() {
  if is_port_open "$GOD_FRONTEND_PORT"; then
    log "Control room already up"
    return 0
  fi

  log "Starting control room"
  : > "$LOG_DIR/Control-room.log"
  # code-server strips this prefix before forwarding requests to Vite.
  local proxy_uri="${VSCODE_PROXY_URI:-}"
  local vite_base="/"
  if [[ -n "$proxy_uri" || -n "${CODE_SERVER_PARENT_PID:-}" || -n "${VSCODE_IPC_HOOK_CLI:-}" ]]; then
    vite_base="/proxy/${GOD_FRONTEND_PORT}/"
  fi
  if [[ -n "${VITE_BASE:-}" ]]; then
    if [[ "$VITE_BASE" == "/" ]]; then
      vite_base="/"
    elif [[ "$VITE_BASE" =~ ^/proxy/([0-9]+)/?$ ]]; then
      vite_base="/proxy/${BASH_REMATCH[1]}/"
    else
      log "Ignoring invalid VITE_BASE; expected / or /proxy/<port>/"
    fi
  fi
  log "Control room Vite base: $vite_base"

  local vite_allowed_host=""
  local vite_hmr_protocol=""
  local vite_hmr_client_port=""
  if [[ -n "$proxy_uri" ]]; then
    local vite_proxy_settings
    vite_proxy_settings="$(
      python3 - "$proxy_uri" <<'PY'
import re
import sys
from urllib.parse import urlsplit

try:
    parsed = urlsplit(sys.argv[1])
    hostname = parsed.hostname or ""
    valid = re.fullmatch(r"[A-Za-z0-9.-]+", hostname) and parsed.scheme in {"http", "https"}
    if valid:
        protocol = "wss" if parsed.scheme == "https" else "ws"
        port = parsed.port or (443 if protocol == "wss" else 80)
        print(hostname, protocol, port, sep="\t")
except ValueError:
    pass
PY
    )"
    IFS=$'\t' read -r vite_allowed_host vite_hmr_protocol vite_hmr_client_port <<< "$vite_proxy_settings"
  fi

  local frontend_cmd
  frontend_cmd="cd $(shell_quote "$BACKEND_ROOT/frontend")"
  frontend_cmd+=" && export VITE_BASE=$(shell_quote "$vite_base")"
  frontend_cmd+=" && export GOD_BACKEND_PORT=$(shell_quote "$GOD_BACKEND_PORT")"
  if [[ -n "$vite_allowed_host" ]]; then
    frontend_cmd+=" && export __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=$(shell_quote "$vite_allowed_host")"
    frontend_cmd+=" && export VITE_HMR_PROTOCOL=$(shell_quote "$vite_hmr_protocol")"
    frontend_cmd+=" && export VITE_HMR_CLIENT_PORT=$(shell_quote "$vite_hmr_client_port")"
  fi
  frontend_cmd+=" && unset VSCODE_PROXY_URI"
  # code-server port proxy often dials 0.0.0.0:<port>; bind all interfaces
  # when path-proxy base is active so ECONNREFUSED 0.0.0.0:5174 cannot happen.
  local vite_host="127.0.0.1"
  if [[ "$vite_base" != "/" ]]; then
    vite_host="0.0.0.0"
  fi
  frontend_cmd+=" && export VITE_HOST=$(shell_quote "$vite_host")"
  frontend_cmd+=" && exec npm run dev -- --host $(shell_quote "$vite_host") --port $(shell_quote "$GOD_FRONTEND_PORT") --base $(shell_quote "$vite_base") >> $(shell_quote "$LOG_DIR/Control-room.log") 2>&1"
  start_detached_service "god-frontend" "$FRONTEND_PID_FILE" "$frontend_cmd"

  wait_for_port "$GOD_FRONTEND_PORT" "Control room" 120
}

stop_all() {
  load_env
  kill_pid_file "$FRONTEND_PID_FILE" "control room"
  kill_pid_file "$BACKEND_PID_FILE" "backend"
  stop_known_screens
  kill_listeners_on_port "$GOD_FRONTEND_PORT"
  kill_listeners_on_port "$GOD_BACKEND_PORT"
  log "Stopped"
}

agent_count() {
  python3 - "$TOWN_DIR/agents.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
    print(len(data) if isinstance(data, list) else 0)
except Exception:
    print(0)
PY
}

print_status() {
  load_env
  log "Control room: $frontend_url"
  log "Backend:      $backend_url"
  local backend_state="down"
  local frontend_state="down"
  is_port_open "$GOD_BACKEND_PORT" && backend_state="up"
  is_port_open "$GOD_FRONTEND_PORT" && frontend_state="up"
  log "Ports: backend $GOD_BACKEND_PORT ($backend_state), control room $GOD_FRONTEND_PORT ($frontend_state)"
  log "Saved AI residents: $(agent_count)"
}

start_all() {
  ensure_env_file
  setup_deps
  start_backend
  start_frontend
  log "GOD is up. Open $frontend_url"
}

reset_town() {
  stop_all
  rm -f "$TOWN_DIR/agents.json"
  log "Removed every saved AI resident"
}

case "${1:-start}" in
  start) start_all ;;
  restart) stop_all; start_all ;;
  stop) stop_all ;;
  status) print_status ;;
  tail)
    load_env
    tail -n 80 -F "$LOG_DIR/Backend.log" "$LOG_DIR/Control-room.log"
    ;;
  setup) load_env; setup_deps ;;
  reset) reset_town ;;
  -h|--help|help) usage ;;
  *) usage; exit 1 ;;
esac
