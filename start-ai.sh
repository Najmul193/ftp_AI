#!/usr/bin/env bash
# Start the AI environment: the platform plus the AI module, fully separate
# from the main system -- its own database container, ports and pid files.
#
#   ./start-ai.sh            start
#   ./start-ai.sh --seed     also apply migrations (platform + AI) and seed
#
# Main system: ./start.sh (DB :55432, API :8099, web :5173) -- untouched.
# AI system:   ./start-ai.sh (DB :55442, API :8199, web :5273).
set -euo pipefail
cd "$(dirname "$0")"

export FTP_AI_MODULE=1
export FTP_DB_CONTAINER="${FTP_DB_CONTAINER:-ftp-postgres-ai}"
export FTP_DB_PORT="${FTP_DB_PORT:-55442}"
export FTP_API_PORT="${FTP_API_PORT:-8199}"
export FTP_WEB_PORT="${FTP_WEB_PORT:-5273}"

# Guard: never point the AI environment at the main system's database.
if [ "$FTP_DB_CONTAINER" = "ftp-postgres" ] || [ "$FTP_DB_PORT" = "55432" ]; then
  echo "refusing: the AI environment must not use the main database (ftp-postgres / :55432)" >&2
  exit 1
fi
if ! grep -q "localhost:${FTP_DB_PORT}/" backend/.env 2>/dev/null; then
  echo "refusing: backend/.env DATABASE_URL does not point at :${FTP_DB_PORT}" >&2
  exit 1
fi

exec ./start.sh "$@"
