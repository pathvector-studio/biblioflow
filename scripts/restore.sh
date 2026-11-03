#!/usr/bin/env bash
# Restore or verify a snapshot made by scripts/backup.sh.
#
#   scripts/restore.sh --check backups/20261104T030000Z
#       Restores db.dump into a throwaway database, compares the row counts with the
#       snapshot's MANIFEST and checks that every PDF the database refers to is in the
#       snapshot's library/. Live data is not touched. Run this regularly: a backup that
#       was never restored is not known to work.
#
#   scripts/restore.sh --yes backups/20261104T030000Z
#       Replaces the live database and data/library with the snapshot. Takes a fresh
#       backup of the current state first (scripts/backup.sh), stops the API and the
#       worker while restoring, and starts them again afterwards.
set -euo pipefail

die() { printf ' ✗ %s\n' "$*" >&2; exit 1; }
ok()  { printf ' ✓ %s\n' "$*"; }

[ $# -eq 2 ] || die "usage: $0 --check|--yes <snapshot-dir>"
mode="$1"
snap="${2%/}"
case "$mode" in --check|--yes) ;; *) die "usage: $0 --check|--yes <snapshot-dir>" ;; esac

cd "$(dirname "$0")/.."
[ -f "$snap/db.dump" ] && [ -d "$snap/library" ] && [ -f "$snap/MANIFEST" ] || die "$snap is not a backup snapshot."
command -v rsync >/dev/null || die "rsync is required."
docker compose ps --status running --services 2>/dev/null | grep -qx biblioflow-db \
  || die "biblioflow-db is not running (docker compose up -d biblioflow-db first)."

db() { docker compose exec -T biblioflow-db sh -c "$1"; }
COUNTS_SQL="select 'users', count(*) from users union all
            select 'workspaces', count(*) from workspaces union all
            select 'papers', count(*) from papers union all
            select 'annotations', count(*) from annotations"

if [ "$mode" = "--check" ]; then
  check_db="biblioflow_restore_check"
  db "dropdb -U \"\$POSTGRES_USER\" --if-exists $check_db && createdb -U \"\$POSTGRES_USER\" $check_db"
  trap 'db "dropdb -U \"\$POSTGRES_USER\" --if-exists '"$check_db"'" >/dev/null 2>&1 || true' EXIT
  db "pg_restore -U \"\$POSTGRES_USER\" -d $check_db --no-owner --exit-on-error" < "$snap/db.dump"
  ok "db.dump restored into $check_db"

  got="$(db "psql -U \"\$POSTGRES_USER\" -d $check_db -At -F= -c \"$COUNTS_SQL\"" | tr -d '\r')"
  want="$(grep -E '^(users|workspaces|papers|annotations)=' "$snap/MANIFEST")"
  [ "$got" = "$want" ] || die "row counts differ from MANIFEST:
  restored: $(echo $got)
  manifest: $(echo $want)"
  ok "row counts match MANIFEST ($(echo $got))"

  missing=0
  while IFS= read -r rel; do
    [ -z "$rel" ] && continue
    if [ ! -f "$snap/library/$rel" ]; then
      printf '   missing: %s\n' "$rel" >&2
      missing=$((missing + 1))
    fi
  done < <(db "psql -U \"\$POSTGRES_USER\" -d $check_db -At -c \"select distinct relative_file_path from papers where has_pdf and relative_file_path is not null\"" | tr -d '\r')
  [ "$missing" -eq 0 ] || die "$missing PDF(s) referenced by the database are missing from $snap/library"
  ok "every PDF referenced by the database is in the snapshot"
  ok "snapshot $snap is restorable"
  exit 0
fi

# --yes: replace live data.
scripts/backup.sh
ok "current state saved before restoring (see above)"

docker compose stop biblioflow-api biblioflow-worker >/dev/null
trap 'docker compose start biblioflow-api biblioflow-worker >/dev/null' EXIT
ok "API and worker stopped"

db "pg_restore -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" --clean --if-exists --no-owner --single-transaction --exit-on-error" < "$snap/db.dump"
ok "database restored"

rsync -a --delete --exclude '.incoming/' "$snap/library/" ./data/library/
ok "library restored"

docker compose start biblioflow-api biblioflow-worker >/dev/null
trap - EXIT
ok "API and worker started; restored $snap"
