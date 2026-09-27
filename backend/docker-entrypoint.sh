#!/bin/sh
# Container start: bring the schema up, ensure the reference data, then serve.
#
# This lives in the image rather than in a hosting dashboard's command field so
# there is exactly one place the boot sequence is written, and no layer of
# shell quoting between it and what runs. A platform that assigns its own port
# injects PORT; everything else has a working default.
#
#   RUN_MIGRATIONS=0  skip alembic (a second instance, or a manual migration)
#   RUN_SEED=0        skip seeding
#
# Both steps are idempotent, so the default is to run them on every start.
set -e

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "==> alembic upgrade head"
  alembic upgrade head
fi

if [ "${RUN_SEED:-1}" = "1" ]; then
  echo "==> seeding reference data"
  python -m app.cli.seed
fi

# Optional AI module: its own migration history and seed, only when enabled.
if [ "${FTP_AI_MODULE:-0}" = "1" ]; then
  if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "==> AI module migrations"
    python -m app.ai.cli.migrate
  fi
  if [ "${RUN_SEED:-1}" = "1" ]; then
    echo "==> AI module seed"
    python -m app.ai.cli.seed_ai
  fi
fi

echo "==> uvicorn on 0.0.0.0:${PORT:-8000}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
