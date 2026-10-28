#!/usr/bin/env bash
# Rebuilds the DEMO database from scratch and reseeds it. Destroys all data in the bats database.
set -euo pipefail
cd "$(dirname "$0")/../backend"
export PATH=/opt/homebrew/opt/postgresql@16/bin:$PATH
OWNER_PW="${BATS_OWNER_PW:-bats_dev_pw}"
APP_PW="${BATS_APP_PW:-bats_app_dev_pw}"

# The limited application role (created once, by the local superuser)
psql -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='bats_app'" | grep -q 1 \
  || psql -d postgres -qc "CREATE ROLE bats_app LOGIN PASSWORD '$APP_PW'"

# Schema and grants are applied as the owner role
PGPASSWORD="$OWNER_PW" psql -h localhost -U bats -d bats -q -v ON_ERROR_STOP=1 \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" -f sql/schema.sql -f sql/roles.sql

rm -f audit_anchor.log   # an old anchor file would (correctly) flag a freshly rebuilt log as tampered
.venv/bin/python -m app.seed
