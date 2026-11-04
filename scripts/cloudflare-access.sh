#!/usr/bin/env bash
# Put Cloudflare Access in front of a BiblioFlow hostname from the CLI (no dashboard clicks).
#
#   CLOUDFLARE_API_TOKEN=... scripts/cloudflare-access.sh papers.example.com me@example.com[,you@example.com]
#   CLOUDFLARE_API_TOKEN=... scripts/cloudflare-access.sh --rotate papers.example.com me@example.com
#
# What it does (safe to re-run; the app's policies are replaced each time):
#   1. creates a service token "biblioflow-<hostname>", or reuses it (--rotate issues a new secret)
#   2. creates or updates the self-hosted Access application for <hostname> (the whole host,
#      /api included) with two policies:
#        allow    the listed e-mail addresses (one-time PIN or any login method of the account)
#        service  the service token, for PAT clients (CLI, scripts, MCP) and health checks
# The service token's secret is printed once, when it is created or rotated. Clients send it as
#   CF-Access-Client-Id: <id>
#   CF-Access-Client-Secret: <secret>
# alongside their usual "Authorization: Bearer bf_pat_...".
#
# Run scripts/cloudflare-tunnel.sh first. The API token needs, on top of what that script needs:
#   Account > Access: Apps and Policies > Edit, Account > Access: Service Tokens > Edit.
# Keep the API token and the service token secret out of .env (.env is passed to the containers).
set -euo pipefail

API="${CF_API_BASE:-https://api.cloudflare.com/client/v4}"

die() { printf ' ✗ %s\n' "$*" >&2; exit 1; }
ok()  { printf ' ✓ %s\n' "$*" >&2; }

rotate=false
if [ "${1:-}" = "--rotate" ]; then rotate=true; shift; fi
[ $# -eq 2 ] || die "usage: CLOUDFLARE_API_TOKEN=... $0 [--rotate] <hostname> <email>[,<email>...]"
HOST="$1"
EMAILS="$2"
[ -n "${CLOUDFLARE_API_TOKEN:-}" ] || die "CLOUDFLARE_API_TOKEN is not set."
command -v curl >/dev/null || die "curl is required."
command -v jq >/dev/null || die "jq is required."
emails_json="$(jq -nc --arg e "$EMAILS" '$e | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))')"
[ "$(jq 'length' <<<"$emails_json")" -gt 0 ] || die "give at least one e-mail address."
jq -e 'all(test("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"))' <<<"$emails_json" >/dev/null || die "not an e-mail address: $EMAILS"

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

# 1. Account (via the zone, as in cloudflare-tunnel.sh)
account_id="" candidate="$HOST"
while [[ "$candidate" == *.* ]]; do
  zones="$(cf GET "/zones?name=${candidate}")"
  if [ "$(jq 'length' <<<"$zones")" -gt 0 ]; then
    account_id="$(jq -r '.[0].account.id' <<<"$zones")"
    break
  fi
  candidate="${candidate#*.}"
done
[ -n "$account_id" ] || die "No Cloudflare zone found for ${HOST} (or the token cannot read it)."
ok "zone ${candidate}"

# 2. Service token
st_name="biblioflow-${HOST}"
st_id="$(cf GET "/accounts/${account_id}/access/service_tokens?name=${st_name}" \
  | jq -r --arg n "$st_name" 'map(select(.name == $n)) | .[0].id // empty')"
secret_json=""
if [ -z "$st_id" ]; then
  secret_json="$(cf POST "/accounts/${account_id}/access/service_tokens" \
    "$(jq -nc --arg n "$st_name" '{name: $n, duration: "8760h"}')")"
  st_id="$(jq -r '.id' <<<"$secret_json")"
  ok "created service token ${st_name} (valid for 1 year)"
elif [ "$rotate" = true ]; then
  secret_json="$(cf POST "/accounts/${account_id}/access/service_tokens/${st_id}/rotate")"
  ok "rotated service token ${st_name} (the old secret stops working)"
else
  ok "reusing service token ${st_name} (secret unchanged; --rotate to issue a new one)"
fi

# 3. Access application
app_body="$(jq -nc --arg h "$HOST" --arg id "$st_id" --argjson emails "$emails_json" '{
  name: ("biblioflow " + $h),
  domain: $h,
  type: "self_hosted",
  session_duration: "24h",
  app_launcher_visible: false,
  policies: [
    {name: "allow", decision: "allow", precedence: 1,
     include: ($emails | map({email: {email: .}}))},
    {name: "service", decision: "non_identity", precedence: 2,
     include: [{service_token: {token_id: $id}}]}
  ]}')"
app_id="$(cf GET "/accounts/${account_id}/access/apps?domain=${HOST}" \
  | jq -r --arg h "$HOST" 'map(select(.domain == $h)) | .[0].id // empty')"
if [ -z "$app_id" ]; then
  cf POST "/accounts/${account_id}/access/apps" "$app_body" >/dev/null
  ok "created Access application for https://${HOST}"
else
  cf PUT "/accounts/${account_id}/access/apps/${app_id}" "$app_body" >/dev/null
  ok "updated Access application for https://${HOST}"
fi
ok "allowed: $(jq -r 'join(", ")' <<<"$emails_json") + service token ${st_name}"

# 4. Check that an anonymous request no longer reaches the origin.
code="$(curl -s -o /dev/null -w '%{http_code}' "https://${HOST}/api/v1/healthz" || true)"
case "$code" in
  302|401|403) ok "anonymous request is stopped by Access (HTTP ${code})" ;;
  *) printf ' ! anonymous request returned HTTP %s; Access can take a minute to apply, check again.\n' "$code" >&2 ;;
esac

if [ -n "$secret_json" ]; then
  printf '\nService token (shown only now; store it with your PAT, not in .env):\n' >&2
  jq -r '"CF_ACCESS_CLIENT_ID=\(.client_id)\nCF_ACCESS_CLIENT_SECRET=\(.client_secret)"' <<<"$secret_json"
fi
