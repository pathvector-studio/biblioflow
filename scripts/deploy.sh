#!/usr/bin/env bash
# Deploy a checkout (typically a dedicated production clone) at a given git ref.
#
#   scripts/deploy.sh               # deploy origin/main
#   scripts/deploy.sh v1.2.0        # deploy a tag / branch / commit (also how you roll back)
#
# Steps: refuse local changes -> fetch -> back up (scripts/backup.sh, if the stack is running)
#        -> check out the ref (detached) -> build -> `docker compose up -d --wait` -> health check
#        through the gateway. Database migrations run when the API container starts.
# The profiles to start (e.g. cloudflare) come from COMPOSE_PROFILES in .env.
# On failure, deploy the previous commit printed at the start to roll back the code. A migration
# that already ran is not rolled back: restore the backup taken here (scripts/restore.sh) if needed.
set -euo pipefail

die() { printf ' ✗ %s\n' "$*" >&2; exit 1; }
ok()  { printf ' ✓ %s\n' "$*"; }

cd "$(dirname "$0")/.."
[ -f .env ] || die ".env not found."
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "the checkout has local changes; deploy from a clean clone."
ref="${1:-origin/main}"

git fetch --quiet --tags origin
target="$(git rev-parse --verify --quiet "${ref}^{commit}")" || die "unknown ref: $ref"
before="$(git rev-parse HEAD)"
ok "deploying $ref ($(git log -1 --format='%h %s' "$target")); previous: $(git log -1 --format=%h "$before")"

if docker compose ps --status running --services 2>/dev/null | grep -qx biblioflow-db; then
  scripts/backup.sh
else
  ok "stack not running: no backup taken"
fi

git checkout --quiet --detach "$target"
docker compose build --quiet
docker compose up -d --wait --wait-timeout 300 || die "services did not become healthy (docker compose ps / logs). Roll back: scripts/deploy.sh $before"

port="$(sed -n 's/^FRONTEND_PORT=//p' .env | tail -1)"
curl -fsS "http://127.0.0.1:${port:-3000}/api/v1/readyz" >/dev/null || die "readyz failed through the gateway. Roll back: scripts/deploy.sh $before"
ok "deployed $(git log -1 --format='%h' HEAD); $(docker compose ps --services --status running | wc -l | tr -d ' ') services running"
