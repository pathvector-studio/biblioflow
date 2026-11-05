#!/usr/bin/env bash
# BiblioFlow installer
#   curl -sSL https://biblioflow.pathvector.dev/install.sh | bash
#
# Clones the BiblioFlow repo, writes a .env with generated secrets,
# then builds and starts the stack with docker compose.
#
# Environment overrides:
#   BIBLIOFLOW_DIR       install directory   (default: ~/biblioflow)
#   BIBLIOFLOW_REPO      git URL             (default: https://github.com/pathvector-studio/biblioflow.git)
#   BIBLIOFLOW_REF       branch or tag       (default: main)
#   BIBLIOFLOW_PORT      port on 127.0.0.1   (default: 3000; only used when .env is created)
#   BIBLIOFLOW_NO_START  set to 1 to prepare files only, without starting containers
set -euo pipefail

# Wrapped in main so a truncated download never runs half a script.
main() {
  local dir="${BIBLIOFLOW_DIR:-$HOME/biblioflow}"
  local repo="${BIBLIOFLOW_REPO:-https://github.com/pathvector-studio/biblioflow.git}"
  local ref="${BIBLIOFLOW_REF:-main}"

  if [ -t 1 ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; RST=$'\033[0m'
  else
    BOLD=''; DIM=''; RED=''; GRN=''; YLW=''; RST=''
  fi
  info() { printf '%s==>%s %s\n' "$BOLD" "$RST" "$*"; }
  ok()   { printf '%s ✓%s %s\n' "$GRN" "$RST" "$*"; }
  warn() { printf '%s !%s %s\n' "$YLW" "$RST" "$*" >&2; }
  die()  { printf '%s ✗%s %s\n' "$RED" "$RST" "$*" >&2; exit 1; }

  gen_secret() {
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex 32
    else
      od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
    fi
  }

  # Replace KEY=... in .env (portable across GNU and BSD sed). Values must not contain '|'.
  set_env() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp .env.XXXXXX)"
    if grep -q "^${key}=" .env; then
      sed "s|^${key}=.*|${key}=${value}|" .env > "$tmp"
    else
      cp .env "$tmp"
      printf '%s=%s\n' "$key" "$value" >> "$tmp"
    fi
    chmod 600 "$tmp"
    mv "$tmp" .env
  }

  get_env() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- | tr -d '"' || true; }

  port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

  printf '\n%s☕ BiblioFlow installer%s\n\n' "$BOLD" "$RST"

  # 1. Requirements
  info "Checking requirements"
  command -v git >/dev/null 2>&1 || die "git is required."
  command -v docker >/dev/null 2>&1 \
    || die "Docker is not installed. Get it at https://docs.docker.com/get-docker/"
  docker compose version >/dev/null 2>&1 \
    || die "Docker Compose v2 is required ('docker compose'). Update Docker or install the compose plugin."
  docker info >/dev/null 2>&1 \
    || die "Cannot reach the Docker daemon. Start Docker (or add your user to the 'docker' group) and retry."
  ok "docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?'), compose $(docker compose version --short 2>/dev/null || echo 'v2')"

  # 2. Source
  if [ -d "$dir/.git" ]; then
    info "Updating existing checkout in ${dir}"
    if [ -n "$(git -C "$dir" status --porcelain --untracked-files=no)" ]; then
      die "${dir} has local changes. Commit or stash them, then retry."
    fi
    git -C "$dir" fetch -q --depth 1 origin "$ref"
    git -C "$dir" checkout -q FETCH_HEAD
  elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
    die "${dir} exists and is not a BiblioFlow checkout. Set BIBLIOFLOW_DIR to another path."
  else
    info "Cloning ${repo} (${ref}) into ${dir}"
    git clone -q --depth 1 --branch "$ref" "$repo" "$dir"
  fi
  cd "$dir"
  if [ ! -f docker-compose.yml ] || [ ! -f .env.example ]; then
    die "docker-compose.yml or .env.example missing in ${dir}"
  fi
  ok "Source ready at $(pwd)"

  # 3. .env
  if [ -f .env ]; then
    ok ".env already exists, leaving it untouched"
  else
    local port="${BIBLIOFLOW_PORT:-3000}"
    case "$port" in ''|*[!0-9]*) die "BIBLIOFLOW_PORT must be a number." ;; esac
    cp .env.example .env
    chmod 600 .env
    set_env POSTGRES_PASSWORD "$(gen_secret)"
    set_env REDIS_PASSWORD "$(gen_secret)"
    set_env JWT_SECRET "$(gen_secret)"
    set_env FRONTEND_PORT "$port"
    set_env CORS_ALLOWED_ORIGINS "http://localhost:${port},http://127.0.0.1:${port}"
    # The containers run as APP_UID:APP_GID and write to ./data (bind mount).
    # On Linux, use the installing user's IDs so both sides can write there.
    # Docker Desktop (macOS / Windows) maps file ownership itself; keep 1000.
    if [ "$(uname -s)" = "Linux" ]; then
      local uid gid
      uid="$(id -u)"; gid="$(id -g)"
      # IDs below 1000 (root, system users) would clash with accounts in the images.
      if [ "$uid" -ge 1000 ]; then set_env APP_UID "$uid"; fi
      if [ "$gid" -ge 1000 ]; then set_env APP_GID "$gid"; fi
    fi
    ok "Created .env with generated secrets"
  fi
  if grep -q '=change-me-' .env; then
    warn ".env still contains change-me-* placeholders. Replace them before exposing BiblioFlow."
  fi

  # ./data must be writable by the container user.
  local app_uid app_gid
  app_uid="$(get_env APP_UID)"; app_uid="${app_uid:-1000}"
  app_gid="$(get_env APP_GID)"; app_gid="${app_gid:-1000}"
  mkdir -p data/library data/watch
  if [ "$(uname -s)" = "Linux" ] && [ "$(stat -c %u data/library)" != "$app_uid" ]; then
    if [ "$(id -u)" = "0" ]; then
      chown -R "${app_uid}:${app_gid}" data
      ok "data/ is owned by ${app_uid}:${app_gid} (the container user)"
    else
      warn "data/ is not owned by APP_UID=${app_uid}; uploads will fail. Fix: sudo chown -R ${app_uid}:${app_gid} ${dir}/data"
    fi
  fi

  # 4. Start
  local port
  port="$(get_env FRONTEND_PORT)"; port="${port:-3000}"

  if [ "${BIBLIOFLOW_NO_START:-0}" = "1" ]; then
    info "BIBLIOFLOW_NO_START=1, skipping start. Run: cd ${dir} && docker compose up -d --build --wait"
    return 0
  fi
  # docker-compose.yml fixes the project name, so another BiblioFlow checkout on this machine
  # would be the same compose project: `up` here would replace its containers. Refuse that.
  local project here there
  project="$(get_env COMPOSE_PROJECT_NAME)"; project="${project:-biblioflow}"
  here="$(pwd -P)"
  there="$(docker ps -a --filter "label=com.docker.compose.project=${project}" \
             --format '{{.Label "com.docker.compose.project.working_dir"}}' | sort -u)"
  if [ -n "$there" ]; then
    local d
    while IFS= read -r d; do
      if [ "$(cd "$d" 2>/dev/null && pwd -P || echo "$d")" != "$here" ]; then
        die "Another BiblioFlow (compose project '${project}') runs from ${d}. Stop it there, or give this one its own names in ${dir}/.env: BF_INSTANCE=biblioflow-2, COMPOSE_PROJECT_NAME=biblioflow-2 and another FRONTEND_PORT."
      fi
    done <<< "$there"
  elif port_in_use "$port"; then
    die "Port ${port} is already in use. Set FRONTEND_PORT and CORS_ALLOWED_ORIGINS in ${dir}/.env to another port (or delete .env and rerun with BIBLIOFLOW_PORT=<free port>), then retry."
  fi
  info "Building and starting BiblioFlow (the first build takes a few minutes)"
  docker compose up -d --build --wait --wait-timeout 300 \
    || die "Some services did not become healthy. See: cd ${dir} && docker compose ps && docker compose logs"

  printf '\n%s🎉 BiblioFlow is running:%s http://localhost:%s\n\n' "$GRN$BOLD" "$RST" "$port"
  printf 'Create your first user (asks for a password):\n'
  printf '  cd %s && docker compose exec biblioflow-api python -m app.cli create-user --email you@example.com --admin\n\n' "$dir"
  printf 'Remote access (Cloudflare Tunnel, Tailscale) and backups: https://github.com/pathvector-studio/biblioflow#readme\n'
  printf '%sAI chat and semantic search are on the roadmap; today an MCP client can search your library (see the README).%s\n' "$DIM" "$RST"
  printf '%sEnjoying it? https://biblioflow.pathvector.dev/#sponsor ☕%s\n\n' "$DIM" "$RST"
}

main "$@"
