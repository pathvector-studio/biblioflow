#!/usr/bin/env bash
# Check that Cloudflare Access protects a hostname before (and after) a tunnel serves it.
#
#   scripts/check-access.sh papers.example.com
#
# Requests a few paths without any credentials and expects every one to be stopped by Access: a redirect to
# <team>.cloudflareaccess.com (browser login) or 401/403. Anything else, such as a 200 from BiblioFlow, means the
# host is reachable without Access, and the script exits 1. Nothing is sent but plain GET requests.
set -euo pipefail

die() { printf 'check-access: %s\n' "$*" >&2; exit 2; }

[ $# -eq 1 ] || die "usage: $0 <hostname>"
host="$1"
case "$host" in
  *[!A-Za-z0-9.-]* | "") die "invalid hostname: $host" ;;
esac

bad=0
for path in "/" "/login" "/api/v1/healthz" "/api/v1/papers/light" "/api/v1/search?q=test"; do
  out="$(curl -s -o /dev/null --max-time 15 -w '%{http_code} %{redirect_url}' "https://${host}${path}" || true)"
  code="${out%% *}"
  location="${out#* }"
  if [[ "$code" =~ ^30[1278]$ && "$location" =~ ^https://[A-Za-z0-9-]+\.cloudflareaccess\.com/ ]]; then
    printf 'ok    %-24s %s -> Access login\n' "$path" "$code"
  elif [ "$code" = 401 ] || [ "$code" = 403 ]; then
    printf 'ok    %-24s %s\n' "$path" "$code"
  else
    printf 'OPEN  %-24s %s %s\n' "$path" "${code:-no response}" "${location:+-> $location}"
    bad=1
  fi
done

if [ "$bad" -ne 0 ]; then
  printf '\n%s is reachable without Cloudflare Access. Stop the tunnel (docker compose --profile cloudflare stop biblioflow-cloudflared)\nand run scripts/cloudflare-access.sh %s <email,...> first.\n' "$host" "$host" >&2
  exit 1
fi
printf '\nCloudflare Access protects %s.\n' "$host"
