#!/usr/bin/env bash
set -euo pipefail

ROOT="/data/jxk/yzb/rebuttal/ScaffoldAgent"
SP2_ROOT="$ROOT/StackPlanner2"
PROFILE="$ROOT/scaffold_agent_lite/profiles/deepseek_8286.env"
CONDA_ENV="${CONDA_ENV:-sp2}"
CONDA_PREFIX_PATH="/data/sp-yzb/miniconda3/envs/$CONDA_ENV"

if [ ! -f "$PROFILE" ]; then
  echo "Missing profile: $PROFILE" >&2
  exit 1
fi

set -a
source "$PROFILE"
set +a

export SP2_API_KEY="${LLM_API_KEY:?LLM_API_KEY is missing in $PROFILE}"
export SP2_BASE_URL="${LLM_BASE_URL:?LLM_BASE_URL is missing in $PROFILE}"
export SP2_MODEL="${LLM_MODEL_BASIC:?LLM_MODEL_BASIC is missing in $PROFILE}"
export DEER_FLOW_CONFIG_PATH="$SP2_ROOT/config.yaml"
export DEER_FLOW_HOME="${DEER_FLOW_HOME:-$SP2_ROOT/.deer-flow}"
export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export NPM_CONFIG_REGISTRY="${NPM_CONFIG_REGISTRY:-https://registry.npmmirror.com}"
export npm_config_registry="${npm_config_registry:-$NPM_CONFIG_REGISTRY}"
export COREPACK_ENABLE_PROJECT_SPEC="${COREPACK_ENABLE_PROJECT_SPEC:-0}"
export PNPM_HOME="${PNPM_HOME:-$CONDA_PREFIX_PATH/bin}"

export PATH="$CONDA_PREFIX_PATH/bin:$PATH"

cd "$SP2_ROOT"

start_manual_daemon() {
  local gateway_port="${GATEWAY_PORT:-18001}"
  local frontend_port="${FRONTEND_PORT:-18002}"
  local nginx_port="${NGINX_PORT:-18003}"

  for port in "$gateway_port" "$frontend_port" "$nginx_port"; do
    if python - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
s = socket.socket()
try:
    s.bind(("127.0.0.1", port))
except OSError:
    raise SystemExit(0)
else:
    s.close()
    raise SystemExit(1)
PY
    then
      echo "Port $port is already in use." >&2
      exit 1
    fi
  done

  export DEER_FLOW_INTERNAL_GATEWAY_BASE_URL="http://127.0.0.1:$gateway_port"
  export DEER_FLOW_TRUSTED_ORIGINS="http://localhost:$nginx_port,http://127.0.0.1:$nginx_port"

  mkdir -p logs temp/client_body_temp temp/proxy_temp temp/fastcgi_temp temp/uwsgi_temp temp/scgi_temp "$DEER_FLOW_HOME" backend/sandbox

  sed \
    -e "s/127\.0\.0\.1:8001/127.0.0.1:$gateway_port/g" \
    -e "s/127\.0\.0\.1:3000/127.0.0.1:$frontend_port/g" \
    -e "s/listen 2026;/listen $nginx_port;/g" \
    -e "s/listen \[::\]:2026;/listen [::]:$nginx_port;/g" \
    "$SP2_ROOT/docker/nginx/nginx.local.conf" > "$SP2_ROOT/logs/nginx.local.generated.conf"

  : > "$SP2_ROOT/logs/gateway.log"
  : > "$SP2_ROOT/logs/frontend.log"
  : > "$SP2_ROOT/logs/nginx.log"

  wait_for_listen() {
    local port="$1"
    local name="$2"
    local timeout="$3"
    local elapsed=0
    while ! python - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket() as s:
    s.settimeout(0.2)
    raise SystemExit(0 if s.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
    do
      if [ "$elapsed" -ge "$timeout" ]; then
        echo "$name failed to listen on port $port after ${timeout}s" >&2
        return 1
      fi
      sleep 1
      elapsed=$((elapsed + 1))
    done
  }

  (
    cd "$SP2_ROOT/backend"
    setsid -f env DEERFLOW_DAEMON_ROOT="$SP2_ROOT" PYTHONPATH=. \
      "$SP2_ROOT/backend/.venv/bin/python" -m uvicorn app.gateway.app:app \
      --host 0.0.0.0 --port "$gateway_port" \
      > "$SP2_ROOT/logs/gateway.log" 2>&1
  )

  (
    cd "$SP2_ROOT/frontend"
    setsid -f env DEERFLOW_DAEMON_ROOT="$SP2_ROOT" \
      "$SP2_ROOT/frontend/node_modules/.bin/next" dev --turbo --port "$frontend_port" \
      > "$SP2_ROOT/logs/frontend.log" 2>&1
  )

  wait_for_listen "$gateway_port" Gateway 120 || {
    tail -80 "$SP2_ROOT/logs/gateway.log" || true
    exit 1
  }
  wait_for_listen "$frontend_port" Frontend 120 || {
    tail -80 "$SP2_ROOT/logs/frontend.log" || true
    exit 1
  }

  setsid -f nginx \
    -g "error_log $SP2_ROOT/logs/nginx-error.log warn; daemon off;" \
    -c "$SP2_ROOT/logs/nginx.local.generated.conf" \
    -p "$SP2_ROOT" \
    > "$SP2_ROOT/logs/nginx.log" 2>&1

  wait_for_listen "$nginx_port" Nginx 20 || {
    tail -80 "$SP2_ROOT/logs/nginx.log" || true
    exit 1
  }

  echo "SP2 is running:"
  echo "  UI:      http://localhost:$nginx_port"
  echo "  Gateway: http://localhost:$gateway_port"
  echo "  Frontend:http://localhost:$frontend_port"
  echo "  Logs:    $SP2_ROOT/logs/{gateway,frontend,nginx}.log"
}

stop_manual_daemon() {
  local gateway_port="${GATEWAY_PORT:-18001}"
  local frontend_port="${FRONTEND_PORT:-18002}"
  local nginx_port="${NGINX_PORT:-18003}"
  local port pid cwd cmd

  for port in "$gateway_port" "$frontend_port" "$nginx_port"; do
    while IFS= read -r pid; do
      [ -n "$pid" ] || continue
      cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || true)
      cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
      case "$cwd $cmd" in
        *"$SP2_ROOT"*) kill "$pid" 2>/dev/null || true ;;
      esac
    done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  done

  sleep 1
  for port in "$gateway_port" "$frontend_port" "$nginx_port"; do
    while IFS= read -r pid; do
      [ -n "$pid" ] || continue
      cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || true)
      cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
      case "$cwd $cmd" in
        *"$SP2_ROOT"*) kill -9 "$pid" 2>/dev/null || true ;;
      esac
    done < <(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)
  done

  echo "Stopped manual SP2 services on ports $gateway_port/$frontend_port/$nginx_port"
}

case "${1:-dev}" in
  smoke)
    "$CONDA_PREFIX_PATH/bin/uv" --directory backend run python - <<'PY'
import os
import httpx

base_url = os.environ["SP2_BASE_URL"].rstrip("/")
api_key = os.environ["SP2_API_KEY"]
model = os.environ["SP2_MODEL"]

response = httpx.post(
    f"{base_url}/chat/completions",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
    json={
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：通过"}],
        "temperature": 0,
        "max_tokens": 64,
        "stream": False,
        "enable_thinking": False,
    },
    timeout=60,
)
print(f"model={model} status={response.status_code}")
response.raise_for_status()
message = response.json()["choices"][0]["message"]
print((message.get("content") or message.get("reasoning") or "").strip()[:120])
PY
    ;;
  check)
    python scripts/check.py
    "$CONDA_PREFIX_PATH/bin/uv" --directory backend sync --all-packages --default-index "$UV_DEFAULT_INDEX"
    pnpm --dir frontend install --config.manage-package-manager-versions=false
    "$CONDA_PREFIX_PATH/bin/uv" --directory backend run python ../scripts/doctor.py
    ;;
  dev)
    bash scripts/serve.sh --dev
    ;;
  dev-daemon)
    bash scripts/serve.sh --dev --daemon
    ;;
  manual-daemon)
    start_manual_daemon
    ;;
  manual-stop)
    stop_manual_daemon
    ;;
  stop)
    bash scripts/serve.sh --stop
    ;;
  *)
    echo "Usage: $0 {smoke|check|dev|dev-daemon|manual-daemon|manual-stop|stop}" >&2
    exit 2
    ;;
esac
