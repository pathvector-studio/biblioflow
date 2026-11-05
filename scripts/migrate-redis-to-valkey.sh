#!/usr/bin/env bash
# One-time step after upgrading to a version that runs Valkey instead of Redis.
#
#   scripts/deploy.sh                  # brings up Valkey with a new, empty volume (valkeydata)
#   scripts/migrate-redis-to-valkey.sh # copies the revoked sessions over from the old Redis volume
#
# Valkey cannot read the data files of Redis 7.4 (RDB format 12), so the old volume is read by a
# temporary Redis container (no network) and only the revoked-session keys (bf:jwt:deny:*) are
# copied, with their remaining TTL. Without this, a session cookie that was logged out before the
# upgrade would be accepted again until it expires. Everything else in Redis is disposable: rate
# limit counters, the job queue (re-run `python -m app.cli reindex --all` if a job was pending) and
# lookup caches. The old volume is left in place; the script prints how to delete it.
# Safe to run more than once.
set -euo pipefail

die() { printf ' ✗ %s\n' "$*" >&2; exit 1; }
ok()  { printf ' ✓ %s\n' "$*"; }

cd "$(dirname "$0")/.."
[ -f .env ] || die ".env not found."
project="$(docker compose config | sed -n 's/^name: //p' | head -1)"
[ -n "$project" ] || die "could not determine the compose project name."
old="${project}_redisdata"
if ! docker volume inspect "$old" >/dev/null 2>&1; then
  ok "no old Redis volume ($old): nothing to migrate"
  exit 0
fi
docker compose exec -T biblioflow-redis sh -c 'valkey-cli -v' >/dev/null 2>&1 \
  || die "biblioflow-redis is not running Valkey yet: deploy the new version first."

copy="bf-redis-migrate-$$"
cleanup() {
  docker rm -f "$copy" >/dev/null 2>&1 || true
  docker volume rm "$copy" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Work on a copy so the old volume stays untouched (Redis rewrites its AOF on start).
docker volume create "$copy" >/dev/null
docker run --rm --network none -v "$old":/src:ro -v "$copy":/dst redis:7-alpine sh -c 'cp -a /src/. /dst/'
docker run -d --name "$copy" --network none -v "$copy":/data redis:7-alpine redis-server --appendonly yes >/dev/null
for _ in $(seq 1 30); do
  [ "$(docker exec "$copy" redis-cli ping 2>/dev/null)" = "PONG" ] && break
  sleep 1
done
[ "$(docker exec "$copy" redis-cli ping 2>/dev/null)" = "PONG" ] || die "the old Redis data could not be loaded."

copied=0
while IFS= read -r key; do
  [ -n "$key" ] || continue
  ttl="$(docker exec "$copy" redis-cli pttl "$key")"
  [ "$ttl" -gt 0 ] 2>/dev/null || continue
  docker compose exec -T biblioflow-redis sh -c \
    'valkey-cli -a "$REDIS_PASSWORD" --no-auth-warning set "$1" 1 px "$2" >/dev/null' _ "$key" "$ttl"
  copied=$((copied + 1))
done < <(docker exec "$copy" redis-cli --scan --pattern 'bf:jwt:deny:*')

ok "copied $copied revoked session(s) from $old to Valkey"
ok "when everything works, delete the old volume: docker volume rm $old"
