#!/usr/bin/env bash
# Stop the AI environment (see start-ai.sh). Options as stop.sh.
set -euo pipefail
cd "$(dirname "$0")"
export FTP_DB_CONTAINER="${FTP_DB_CONTAINER:-ftp-postgres-ai}"
export FTP_API_PORT="${FTP_API_PORT:-8199}"
export FTP_WEB_PORT="${FTP_WEB_PORT:-5273}"
if [ "$FTP_DB_CONTAINER" = "ftp-postgres" ] || [ "$FTP_API_PORT" = "8099" ] || [ "$FTP_WEB_PORT" = "5173" ]; then
  echo "refusing: stop-ai.sh must not touch the main system (ftp-postgres / :8099 / :5173)" >&2
  exit 1
fi
exec ./stop.sh "$@"
