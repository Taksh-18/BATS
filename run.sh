#!/usr/bin/env bash
# Starts PostgreSQL (if needed), the API on :8000 and the frontend on :3000.
set -e
cd "$(dirname "$0")"
export PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH
pg_isready -q || brew services start postgresql@16
(cd backend && .venv/bin/uvicorn app.main:app --port 8000) &
(cd frontend && python3 -m http.server 3000) &
echo "BATS running: http://localhost:3000   (Ctrl+C to stop)"
trap 'kill 0' INT TERM
wait
