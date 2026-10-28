#!/usr/bin/env bash
# Starts PostgreSQL (if needed) and BATS. The API serves the web app, so one server does it all.
set -e
cd "$(dirname "$0")/backend"
export PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH
pg_isready -q || brew services start postgresql@16
echo "BATS running: http://localhost:8000   (Ctrl+C to stop)"
exec .venv/bin/uvicorn app.main:app --port 8000 --no-proxy-headers  # our TRUSTED_PROXY_COUNT setting decides who to believe
