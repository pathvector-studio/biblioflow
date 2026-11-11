#!/usr/bin/env bash
# Run the backend tests of the current worktree without touching the dev stack.
#
# - builds the worktree's backend as biblioflow-loop-api:dev (the dev image stays as it is)
# - uses the database biblioflow_loop_test and Valkey DB 14 (dev tests use biblioflow_test and DB 15)
# - needs the dev stack's biblioflow-db and biblioflow-redis running; starts nothing, stops nothing
#
# Usage: scripts/loop-test.sh [pytest args...]     e.g. scripts/loop-test.sh tests/test_export.py -x
set -euo pipefail

root=$(git rev-parse --show-toplevel)
# The first worktree is the main checkout; it holds .env (compose reads it from the project directory).
main=$(git -C "$root" worktree list --porcelain | awk 'NR==1 {print $2}')
export LOOP_BACKEND="$root/backend"
compose=(docker compose --project-directory "$main" -f "$main/docker-compose.yml" -f "$root/deploy/compose.loop-test.yml")

if [ -z "$("${compose[@]}" ps -q --status running biblioflow-db)" ]; then
  echo "loop-test: biblioflow-db is not running. Start the dev stack first (docker compose up -d)." >&2
  exit 2
fi

"${compose[@]}" exec -T biblioflow-db sh -c '
  psql -U "$POSTGRES_USER" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '"'"'biblioflow_loop_test'"'"'" | grep -q 1 && exit 0
  createdb -U "$POSTGRES_USER" biblioflow_loop_test
  psql -q -U "$POSTGRES_USER" -d biblioflow_loop_test \
    -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS citext;"'

"${compose[@]}" build -q biblioflow-api

# TEST_DATABASE_URL is built by compose; only its database name is swapped, inside the container (no values printed).
exec "${compose[@]}" run --rm --no-deps \
  -v "$root/backend/app:/app/app:ro" -v "$root/backend/tests:/app/tests:ro" -v "$root/backend/alembic:/app/alembic:ro" -v "$root/deploy:/deploy:ro" \
  -e ENABLE_DOCS=false -e TEST_REDIS_DB=14 -e CADDYFILE=/deploy/Caddyfile \
  --entrypoint sh biblioflow-api \
  -c 'export TEST_DATABASE_URL="${TEST_DATABASE_URL%/*}/biblioflow_loop_test"; exec pytest -q -p no:cacheprovider "$@"' sh "$@"
