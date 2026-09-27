#!/usr/bin/env bash
# Stop the FTP platform.
#
#   ./stop.sh          stop the API and web UI, leave PostgreSQL running
#   ./stop.sh --all    also stop the PostgreSQL container
#   ./stop.sh --docker stop the docker compose stack
#   ./stop.sh --purge  also REMOVE the container and its data (destructive)

set -uo pipefail
cd "$(dirname "$0")"

RUN_DIR="var/run"
DB_CONTAINER="${FTP_DB_CONTAINER:-ftp-postgres}"
API_PORT="${FTP_API_PORT:-8099}"
WEB_PORT="${FTP_WEB_PORT:-5173}"

STOP_DB=0
PURGE=0
DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --all) STOP_DB=1 ;;
    --docker) DOCKER=1 ;;
    --purge) STOP_DB=1; PURGE=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[36m==>\033[0m %s\n' "$1"; }
ok()   { printf '    \033[32mok\033[0m  %s\n' "$1"; }
skip() { printf '    \033[90m--\033[0m  %s\n' "$1"; }

stop_pid() {
  local name="$1" file="$RUN_DIR/$2"
  if [ ! -f "$file" ]; then skip "$name not running"; return; fi
  local pid; pid="$(cat "$file")"
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$file"; skip "$name not running (stale pid file removed)"; return
  fi
  # Ask politely first so in-flight requests finish, then insist.
  kill "$pid" 2>/dev/null
  for _ in $(seq 1 10); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null
    ok "$name force-stopped (pid $pid)"
  else
    ok "$name stopped (pid $pid)"
  fi
  rm -f "$file"
}

if [ "$DOCKER" = "1" ]; then
  printf '\033[36m==>\033[0m %s\n' "Docker Compose"
  if [ "$PURGE" = "1" ]; then
    docker compose down -v && printf '    ok  stack and volumes removed\n'
  else
    docker compose down && printf '    ok  stack stopped (volumes kept)\n'
  fi
  echo; printf '\033[32mFTP platform stopped.\033[0m\n'
  exit 0
fi

say "Stopping services"
stop_pid "Web UI" web.pid
stop_pid "API" api.pid

# Catch anything started outside the scripts, so a stray dev server does not
# hold the port and make the next start fail confusingly. Matched on this
# environment's own ports only: a second environment (start-ai.sh) runs the
# same commands on other ports, and stopping one must never stop the other.
pkill -f "uvicorn app.main:app .*--port ${API_PORT}( |$)" 2>/dev/null \
  && ok "stray uvicorn on :${API_PORT} cleared"
pkill -f "vite .*--port ${WEB_PORT}( |$)" 2>/dev/null \
  && ok "stray vite on :${WEB_PORT} cleared"

if [ "$STOP_DB" = "1" ]; then
  say "PostgreSQL"
  if command -v docker >/dev/null 2>&1; then
    if [ "$PURGE" = "1" ]; then
      printf '    \033[31mThis deletes the database and all loaded data.\033[0m\n'
      read -r -p "    Type the container name to confirm (${DB_CONTAINER}): " reply
      if [ "$reply" = "$DB_CONTAINER" ]; then
        docker rm -f "$DB_CONTAINER" >/dev/null 2>&1 && ok "container removed with its data"
      else
        skip "purge cancelled"
      fi
    else
      docker stop "$DB_CONTAINER" >/dev/null 2>&1 && ok "container stopped (data kept)" \
        || skip "container was not running"
    fi
  else
    skip "docker not found"
  fi
else
  skip "PostgreSQL left running (use --all to stop it)"
fi

echo
printf '\033[32mFTP platform stopped.\033[0m\n'
