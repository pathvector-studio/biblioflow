#!/usr/bin/env bash
# Back up BiblioFlow: the PostgreSQL database and the PDF library.
#
#   scripts/backup.sh                 # -> ${BACKUP_DIR:-./backups}/<UTC timestamp>/
#   BACKUP_DIR=/mnt/nas/biblioflow BACKUP_KEEP=30 scripts/backup.sh
#
# Each snapshot contains:
#   db.dump      pg_dump custom format (restore with scripts/restore.sh)
#   library/     copy of data/library; unchanged PDFs are hard links to the previous
#                snapshot (rsync --link-dest), so daily snapshots cost little space
#   MANIFEST     row counts and file counts, used by `scripts/restore.sh --check`
# The newest BACKUP_KEEP (default 14) snapshots are kept; older ones are deleted.
#
# Not included: .env (keep the secrets somewhere safe yourself), Redis (only rate
# limits, revoked sessions and the job queue).
# Put BACKUP_DIR on another disk or machine; a backup on the same disk is not a backup.
# The stack must be running (the dump is taken from the biblioflow-db container).
set -euo pipefail

die() { printf ' ✗ %s\n' "$*" >&2; exit 1; }
ok()  { printf ' ✓ %s\n' "$*"; }

cd "$(dirname "$0")/.."
command -v rsync >/dev/null || die "rsync is required."
docker compose ps --status running --services 2>/dev/null | grep -qx biblioflow-db \
  || die "biblioflow-db is not running (docker compose up -d first)."

BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
LIBRARY="./data/library"
case "$BACKUP_KEEP" in ''|*[!0-9]*|0) die "BACKUP_KEEP must be a positive integer." ;; esac

mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="$BACKUP_DIR/$stamp"
tmp="$BACKUP_DIR/.$stamp.partial"
[ ! -e "$dest" ] || die "snapshot $dest already exists (run again in a second)."
rm -rf "$tmp"
mkdir -p "$tmp"
trap 'rm -rf "$tmp"' EXIT
umask 077

# 1. Database first: the library copy taken afterwards then holds every file the dump refers to
#    (files are written before their row is committed).
docker compose exec -T biblioflow-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$tmp/db.dump"
[ -s "$tmp/db.dump" ] || die "pg_dump produced an empty file."
ok "database dumped ($(du -h "$tmp/db.dump" | cut -f1))"

# 2. Library, hard-linking unchanged files to the previous snapshot.
prev="$(ls -1d "$BACKUP_DIR"/2*Z 2>/dev/null | sort | tail -n 1 || true)"
link=()
if [ -n "$prev" ] && [ -d "$prev/library" ]; then
  link=(--link-dest "$(cd "$prev/library" && pwd)")
fi
rsync -a --exclude '.incoming/' ${link[@]+"${link[@]}"} "$LIBRARY/" "$tmp/library/"
ok "library copied ($(find "$tmp/library" -type f -name '*.pdf' | wc -l | tr -d ' ') PDFs)"

# 3. Manifest for restore checks.
counts="$(docker compose exec -T biblioflow-db sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F= -c "
     select '"'"'users'"'"', count(*) from users union all
     select '"'"'workspaces'"'"', count(*) from workspaces union all
     select '"'"'papers'"'"', count(*) from papers union all
     select '"'"'annotations'"'"', count(*) from annotations"')"
{
  echo "created=$stamp"
  echo "$counts" | tr -d '\r'
  echo "pdf_files=$(find "$tmp/library" -type f -name '*.pdf' | wc -l | tr -d ' ')"
} > "$tmp/MANIFEST"

mv "$tmp" "$dest"
trap - EXIT
ok "snapshot $dest"

# 4. Retention.
ls -1d "$BACKUP_DIR"/2*Z 2>/dev/null | sort | awk -v keep="$BACKUP_KEEP" '{a[NR]=$0} END {for (i = 1; i <= NR - keep; i++) print a[i]}' | while read -r old; do
  rm -rf "$old"
  ok "removed old snapshot $old"
done
