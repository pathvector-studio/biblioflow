#!/usr/bin/env bash
# Run a command with the variables of an env file that lives outside the repository.
#
#   scripts/with-env.sh ~/.config/biblioflow/cloudflare.env -- scripts/cloudflare-access.sh papers.example.com you@example.com
#   scripts/with-env.sh ~/.config/biblioflow/mcp.env -- uv run --directory mcp biblioflow-mcp
#
# If the file holds 1Password references (KEY=op://vault/item/field), the command runs under `op run`, which
# resolves them and masks the values in the command's output. Otherwise the file is read as plain KEY=value lines.
# Nothing is printed: neither the variable names nor their values.
set -euo pipefail

die() { printf 'with-env: %s\n' "$*" >&2; exit 1; }

if [ $# -lt 3 ] || [ "$2" != "--" ]; then die "usage: $0 <env-file> -- <command> [args...]"; fi
file="$1"
shift 2
[ -f "$file" ] || die "$file not found (create it with scripts/dev-setup.sh)"

# Refuse a file that other users can read: it holds secrets.
mode="$(stat -c '%a' "$file" 2>/dev/null || stat -f '%Lp' "$file")"  # GNU first: GNU `stat -f` means --file-system
case "$mode" in
  600 | 400) ;;
  *) die "$file has mode $mode; run: chmod 600 $file" ;;
esac

if grep -qE '^[A-Za-z_][A-Za-z0-9_]*=["'\'']?op://' "$file"; then
  command -v op >/dev/null || die "$file uses op:// references but the 1Password CLI (op) is not installed"
  exec op run --env-file="$file" -- "$@"
fi

set -a
# shellcheck disable=SC1090
. "$file"
set +a
exec "$@"
