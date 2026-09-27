#!/usr/bin/env bash
# Start the FTP platform: database, API, and web UI.
#
#   ./start.sh            start everything from local source
#   ./start.sh --seed     also (re)apply migrations and seed reference data
#   ./start.sh --logs     start, then tail the logs
#   ./start.sh --docker   run the whole stack in containers (docker compose)
#
# Each service writes a pid file into var/ so stop.sh can shut it down cleanly.

set -euo pipefail
cd "$(dirname "$0")"

RUN_DIR="var/run"
LOG_DIR="var/log"
mkdir -p "$RUN_DIR" "$LOG_DIR"

DB_CONTAINER="${FTP_DB_CONTAINER:-ftp-postgres}"
DB_PORT="${FTP_DB_PORT:-55432}"
API_PORT="${FTP_API_PORT:-8099}"
WEB_PORT="${FTP_WEB_PORT:-5173}"

SEED=0
TAIL=0
DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --seed) SEED=1 ;;
    --logs) TAIL=1 ;;
    --docker) DOCKER=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[36m==>\033[0m %s\n' "$1"; }
ok()   { printf '    \033[32mok\033[0m  %s\n' "$1"; }
fail() { printf '    \033[31mxx\033[0m  %s\n' "$1" >&2; }

# --- containerised: one command, the same images that deploy to Render -----
if [ "$DOCKER" = "1" ]; then
  say "Docker Compose"
  docker compose up --build -d
  printf '    waiting for API'
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
      printf '\n'; ok "http://127.0.0.1:${API_PORT}"; break
    fi
    printf '.'; sleep 2
  done
  echo
  printf '\033[32mFTP platform is up (containers).\033[0m\n'
  printf '  Dashboard   http://127.0.0.1:%s\n' "$WEB_PORT"
  printf '  API docs    http://127.0.0.1:%s/docs\n' "$API_PORT"
  printf '  Logs        docker compose logs -f\n'
  printf '  Stop        ./stop.sh --docker\n'
  exit 0
fi

# --- already running? ------------------------------------------------------
running() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

if running "$RUN_DIR/api.pid" || running "$RUN_DIR/web.pid"; then
  fail "already running — run ./stop.sh first"
  exit 1
fi

# --- 1. database -----------------------------------------------------------
say "PostgreSQL"
if ! command -v docker >/dev/null 2>&1; then
  fail "docker not found; start PostgreSQL yourself on port $DB_PORT"
elif docker ps --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  ok "container already up"
elif docker ps -a --format '{{.Names}}' | grep -qx "$DB_CONTAINER"; then
  docker start "$DB_CONTAINER" >/dev/null && ok "container restarted"
else
  docker run -d --name "$DB_CONTAINER" \
    -e POSTGRES_PASSWORD=ftp_dev -e POSTGRES_USER=ftp -e POSTGRES_DB=ftp \
    -p "${DB_PORT}:5432" postgres:16 >/dev/null
  ok "container created"
fi

# Wait for it to accept connections rather than assuming a fixed sleep.
printf '    waiting for database'
for _ in $(seq 1 30); do
  if docker exec "$DB_CONTAINER" pg_isready -U ftp -q 2>/dev/null; then
    printf '\n'; ok "accepting connections on :$DB_PORT"; break
  fi
  printf '.'; sleep 1
done

# --- 2. backend ------------------------------------------------------------
say "API"
cd backend
if [ ! -x .venv/bin/python ]; then
  fail "no virtualenv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

if [ "$SEED" = "1" ]; then
  ./.venv/bin/alembic upgrade head >/dev/null && ok "migrations applied"
  ./.venv/bin/python -m app.cli.seed >/dev/null && ok "reference data seeded"
  # The optional AI module keeps its own migration history; only when it is on.
  if [ "${FTP_AI_MODULE:-0}" = "1" ]; then
    ./.venv/bin/python -m app.ai.cli.migrate >/dev/null && ok "AI migrations applied"
    ./.venv/bin/python -m app.ai.cli.seed_ai >/dev/null && ok "AI module seeded"
  fi
fi

nohup ./.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 --port "$API_PORT" --log-level info \
  > "../$LOG_DIR/api.log" 2>&1 &
echo $! > "../$RUN_DIR/api.pid"
cd ..

printf '    waiting for API'
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1; then
    printf '\n'; ok "http://127.0.0.1:${API_PORT}  (docs at /docs)"; break
  fi
  printf '.'; sleep 1
done

# --- 3. frontend -----------------------------------------------------------
say "Web UI"
cd frontend
if [ ! -d node_modules ]; then
  fail "dependencies missing — run: npm install"
  exit 1
fi
# Bind IPv4 explicitly: Vite defaults to [::1] only, which makes a
# 127.0.0.1 health check (and some browsers) fail confusingly.
nohup npx vite --host 127.0.0.1 --port "$WEB_PORT" --strictPort \
  > "../$LOG_DIR/web.log" 2>&1 &
echo $! > "../$RUN_DIR/web.pid"
cd ..

printf '    waiting for web'
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${WEB_PORT}" >/dev/null 2>&1; then
    printf '\n'; ok "http://127.0.0.1:${WEB_PORT}"; break
  fi
  printf '.'; sleep 1
done

echo
printf '\033[32mFTP platform is up.\033[0m\n'
printf '  Dashboard   http://127.0.0.1:%s\n' "$WEB_PORT"
printf '  API docs    http://127.0.0.1:%s/docs\n' "$API_PORT"
printf '  Logs        %s/api.log, %s/web.log\n' "$LOG_DIR" "$LOG_DIR"
printf '  Stop        ./stop.sh\n'

if [ "$TAIL" = "1" ]; then
  echo
  tail -f "$LOG_DIR/api.log" "$LOG_DIR/web.log"
fi
