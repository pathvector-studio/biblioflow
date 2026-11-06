#!/usr/bin/env bash
# Prepare a development checkout. Secrets and machine-specific notes live outside the repository, in
# ${BIBLIOFLOW_CONFIG_DIR:-~/.config/biblioflow}, and the checkout only links to them:
#
#   .env             -> <config>/<name>.env        (compose reads it; database / Valkey / JWT secrets, ports, origins)
#   AGENTS.local.md  -> <config>/AGENTS.local.md   (notes about this machine for coding agents)
#   <config>/mcp.env, <config>/cloudflare.env      (passed only to the commands that need them: scripts/with-env.sh)
#
#   scripts/dev-setup.sh                         # first run: generate secrets; later runs only check
#   scripts/dev-setup.sh --port 3080 --bind 0.0.0.0 --origin http://192.168.1.10:3080
#
# Options (first run only; afterwards edit <config>/<name>.env):
#   --port N        FRONTEND_PORT (default 3000)
#   --bind ADDR     BIND_ADDRESS (default 127.0.0.1)
#   --origin URL    added to CORS_ALLOWED_ORIGINS (repeatable; http://localhost:<port> is always there)
#   --name NAME     env file name, <config>/<NAME>.env (default dev)
#
# Safe to run again. An existing .env in the checkout is moved to the config directory, never overwritten.
# Values are never printed.
set -euo pipefail

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
ok() { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*" >&2; }

port=3000
bind=127.0.0.1
name=dev
origins=()
while [ $# -gt 0 ]; do
  case "$1" in
    --port) port="${2:?}"; shift 2 ;;
    --bind) bind="${2:?}"; shift 2 ;;
    --origin) origins+=("${2:?}"); shift 2 ;;
    --name) name="${2:?}"; shift 2 ;;
    -h | --help) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done
[[ "$port" =~ ^[0-9]+$ ]] || die "--port must be a number"
[[ "$name" =~ ^[A-Za-z0-9_-]+$ ]] || die "--name may contain letters, digits, - and _ only"

cd "$(dirname "$0")/.."
[ -f .env.example ] && [ -f docker-compose.yml ] || die "run this from a BiblioFlow checkout"

config="${BIBLIOFLOW_CONFIG_DIR:-$HOME/.config/biblioflow}"
env_file="$config/$name.env"
umask 077
mkdir -p "$config"
chmod 700 "$config"

# Replace KEY=... in a file, or append it. Writes through symlinks and keeps the file's mode.
set_env() {
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp "$config/.tmp.XXXXXX")"
  awk -v k="$key" -v v="$value" '
    BEGIN { done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$file" > "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

# Move a file of the checkout into the config directory and leave a symlink behind.
link_into_config() {
  local here="$1" there="$2"
  if [ -L "$here" ]; then
    [ "$(readlink "$here")" = "$there" ] || die "$here links somewhere else ($(readlink "$here")); remove the link and rerun"
    [ -e "$there" ] || die "$here links to $there, which does not exist"
    return 0
  fi
  if [ -e "$here" ]; then
    [ -e "$there" ] && die "both $here and $there exist; compare them, keep one, and rerun"
    mv "$here" "$there"
    ok "moved $here to $there"
  fi
  [ -e "$there" ] || return 1
  ln -s "$there" "$here"
  ok "$here -> $there"
}

# 1. .env
if ! link_into_config .env "$env_file"; then
  cp .env.example "$env_file"
  for key in POSTGRES_PASSWORD REDIS_PASSWORD JWT_SECRET; do
    set_env "$env_file" "$key" "$(openssl rand -hex 32)"
  done
  cors="http://localhost:${port}"
  for o in "${origins[@]+"${origins[@]}"}"; do cors="${cors},${o%/}"; done
  set_env "$env_file" FRONTEND_PORT "$port"
  set_env "$env_file" BIND_ADDRESS "$bind"
  set_env "$env_file" CORS_ALLOWED_ORIGINS "$cors"
  ln -s "$env_file" .env
  ok "created $env_file with generated secrets; .env -> $env_file"
elif [ ${#origins[@]} -gt 0 ] || [ "$port" != 3000 ] || [ "$bind" != 127.0.0.1 ]; then
  warn "$env_file already exists; --port / --bind / --origin were ignored (edit the file instead)"
fi
chmod 600 "$env_file"

# 2. AGENTS.local.md
if ! link_into_config AGENTS.local.md "$config/AGENTS.local.md"; then
  cat > "$config/AGENTS.local.md" <<'EOF'
# AGENTS.local.md — notes about this machine (outside the repository; never committed)

- Ports and exposure: FRONTEND_PORT / BIND_ADDRESS in .env, and the URLs to open (they must be in CORS_ALLOWED_ORIGINS).
- Other services on this machine that hold ports BiblioFlow would use.
- Test users (e-mail only; passwords are not written down anywhere).
- Dev hostname behind Cloudflare Tunnel + Access, if this machine runs the tunnel.
EOF
  ln -s "$config/AGENTS.local.md" AGENTS.local.md
  ok "created $config/AGENTS.local.md (fill it in); AGENTS.local.md -> it"
fi

# 3. Files that are passed only to the commands that need them (scripts/with-env.sh).
if [ ! -e "$config/mcp.env" ]; then
  cat > "$config/mcp.env" <<'EOF'
# biblioflow-mcp: scripts/with-env.sh ~/.config/biblioflow/mcp.env -- uv run --directory mcp biblioflow-mcp
# Plain values, or 1Password references (op://<vault>/<item>/<field>), then the command runs under `op run`.
BIBLIOFLOW_URL=http://localhost:3000
BIBLIOFLOW_TOKEN=
# Only behind Cloudflare Access (the service token printed by scripts/cloudflare-access.sh):
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
EOF
  ok "created $config/mcp.env (fill in a personal access token)"
fi
if [ ! -e "$config/cloudflare.env" ]; then
  cat > "$config/cloudflare.env" <<'EOF'
# scripts/cloudflare-tunnel.sh / cloudflare-access.sh:
#   scripts/with-env.sh ~/.config/biblioflow/cloudflare.env -- scripts/cloudflare-access.sh <hostname> <email,...>
# Plain value, or a 1Password reference (op://<vault>/<item>/<field>).
CLOUDFLARE_API_TOKEN=
EOF
  ok "created $config/cloudflare.env"
fi
chmod 600 "$config/mcp.env" "$config/cloudflare.env"

# 4. git hook that refuses private details in commits.
git config core.hooksPath .githooks
ok "git hooks: .githooks"

# 5. Report what still needs attention (names only).
if grep -q '=change-me-' "$env_file"; then
  warn "$env_file still has change-me-* values: $(grep -oE '^[A-Z_]+=change-me-' "$env_file" | cut -d= -f1 | tr '\n' ' ')"
fi
missing="$(comm -23 <(grep -oE '^[A-Z][A-Z0-9_]*=' .env.example | sort -u) <(grep -oE '^[A-Z][A-Z0-9_]*=' "$env_file" | sort -u) | tr -d = | tr '\n' ' ')"
[ -z "$missing" ] || warn "not in $env_file (the defaults apply): $missing"
extra="$(comm -13 <(grep -oE '^#? ?[A-Z][A-Z0-9_]*=' .env.example | sed 's/^# \{0,1\}//' | sort -u) <(grep -oE '^[A-Z][A-Z0-9_]*=' "$env_file" | sort -u) | tr -d = | tr '\n' ' ')"
[ -z "$extra" ] || warn "in $env_file but no longer used: $extra"
if grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+' "$env_file"; then
  warn "this machine runs the Cloudflare tunnel: check Access first with scripts/check-access.sh <hostname>"
fi

printf '\nNext: docker compose up -d --build   (see LOCAL_DEV.md)\n'
