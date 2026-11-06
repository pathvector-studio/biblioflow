#!/usr/bin/env bash
# Set up a Cloudflare Tunnel for BiblioFlow from the CLI (no dashboard clicks).
#
#   CLOUDFLARE_API_TOKEN=... scripts/cloudflare-tunnel.sh papers.example.com [tunnel-name]
#
# What it does (safe to re-run):
#   1. finds the Cloudflare zone and account for the hostname
#   2. creates a remotely-managed tunnel (default name: biblioflow), or reuses it
#   3. routes https://<hostname> -> http://biblioflow-gateway:8081 (trusted listener)
#   4. creates the proxied CNAME <hostname> -> <tunnel-id>.cfargotunnel.com
#   5. writes CLOUDFLARE_TUNNEL_TOKEN and the https origin in CORS_ALLOWED_ORIGINS
#      into .env
# Then start it with: docker compose --profile cloudflare up -d
#
# The API token needs: Account > Cloudflare Tunnel > Edit,
#                      Zone > DNS > Edit and Zone > Zone > Read (for the zone).
# Keep the API token out of .env: .env is passed to the app containers.
set -euo pipefail

API="${CF_API_BASE:-https://api.cloudflare.com/client/v4}"
SERVICE="http://biblioflow-gateway:8081"

die() { printf ' ✗ %s\n' "$*" >&2; exit 1; }
ok()  { printf ' ✓ %s\n' "$*"; }

[ $# -ge 1 ] || die "usage: CLOUDFLARE_API_TOKEN=... $0 <hostname> [tunnel-name]"
HOST="$1"
NAME="${2:-biblioflow}"
[ -n "${CLOUDFLARE_API_TOKEN:-}" ] || die "CLOUDFLARE_API_TOKEN is not set."
command -v curl >/dev/null || die "curl is required."
command -v jq >/dev/null || die "jq is required."

cd "$(dirname "$0")/.."
[ -f .env ] || die ".env not found. Run: cp .env.example .env (and fill in the secrets) first."

# cf METHOD PATH [JSON] -> prints .result, exits on API error
cf() {
  local method="$1" path="$2" body="${3:-}" res
  res="$(curl -sS -X "$method" "${API}${path}" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -H "Content-Type: application/json" \
    ${body:+--data "$body"})" || die "request failed: $method $path"
  if [ "$(jq -r '.success' <<<"$res")" != "true" ]; then
    die "Cloudflare API error on $method $path: $(jq -c '.errors' <<<"$res")"
  fi
  jq -c '.result' <<<"$res"
}

# 1. Zone and account: try the hostname, then strip labels from the left.
zone_id="" account_id="" candidate="$HOST"
while [[ "$candidate" == *.* ]]; do
  zones="$(cf GET "/zones?name=${candidate}")"
  if [ "$(jq 'length' <<<"$zones")" -gt 0 ]; then
    zone_id="$(jq -r '.[0].id' <<<"$zones")"
    account_id="$(jq -r '.[0].account.id' <<<"$zones")"
    break
  fi
  candidate="${candidate#*.}"
done
[ -n "$zone_id" ] || die "No Cloudflare zone found for ${HOST} (or the token cannot read it)."
ok "zone ${candidate}"

# 2. Tunnel
tunnels="$(cf GET "/accounts/${account_id}/cfd_tunnel?name=${NAME}&is_deleted=false")"
tunnel_id="$(jq -r '.[0].id // empty' <<<"$tunnels")"
if [ -z "$tunnel_id" ]; then
  tunnel_id="$(cf POST "/accounts/${account_id}/cfd_tunnel" \
    "$(jq -nc --arg n "$NAME" '{name: $n, config_src: "cloudflare"}')" | jq -r '.id')"
  ok "created tunnel ${NAME} (${tunnel_id})"
else
  ok "reusing tunnel ${NAME} (${tunnel_id})"
fi

# 3. Ingress (this tunnel is dedicated to BiblioFlow; the rule set is replaced)
cf PUT "/accounts/${account_id}/cfd_tunnel/${tunnel_id}/configurations" \
  "$(jq -nc --arg h "$HOST" --arg s "$SERVICE" \
    '{config: {ingress: [{hostname: $h, service: $s}, {service: "http_status:404"}]}}')" >/dev/null
ok "route https://${HOST} -> ${SERVICE}"

# 4. DNS
target="${tunnel_id}.cfargotunnel.com"
records="$(cf GET "/zones/${zone_id}/dns_records?name=${HOST}")"
if [ "$(jq 'length' <<<"$records")" -eq 0 ]; then
  cf POST "/zones/${zone_id}/dns_records" \
    "$(jq -nc --arg n "$HOST" --arg c "$target" \
      '{type: "CNAME", name: $n, content: $c, proxied: true, comment: "BiblioFlow tunnel"}')" >/dev/null
  ok "DNS ${HOST} CNAME ${target}"
elif [ "$(jq -r '.[0].type + " " + .[0].content' <<<"$records")" = "CNAME ${target}" ]; then
  ok "DNS ${HOST} already points to the tunnel"
else
  die "DNS record for ${HOST} already exists ($(jq -r '.[0].type + " " + .[0].content' <<<"$records")). Remove it or pick another hostname."
fi

# 5. .env
token="$(cf GET "/accounts/${account_id}/cfd_tunnel/${tunnel_id}/token" | jq -r '.')"
[ -n "$token" ] && [ "$token" != "null" ] || die "Could not fetch the tunnel token."

set_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp .env.XXXXXX)"
  awk -v k="$key" -v v="$value" '
    BEGIN { done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' .env > "$tmp"
  # Write through: .env may be a symlink to a file outside the checkout (scripts/dev-setup.sh).
  cat "$tmp" > .env
  rm -f "$tmp"
  chmod 600 .env
}

set_env CLOUDFLARE_TUNNEL_TOKEN "$token"
origins="$(grep -E '^CORS_ALLOWED_ORIGINS=' .env | tail -1 | cut -d= -f2- || true)"
case ",${origins}," in
  *",https://${HOST},"*) ;;
  *) set_env CORS_ALLOWED_ORIGINS "${origins:+${origins},}https://${HOST}" ;;
esac
ok ".env updated (CLOUDFLARE_TUNNEL_TOKEN, CORS_ALLOWED_ORIGINS)"

printf '\nNext:\n  docker compose --profile cloudflare up -d\n  open https://%s\n\n' "$HOST"
