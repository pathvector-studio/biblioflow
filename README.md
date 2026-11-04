# BiblioFlow

[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/pathvector-studio)

A self-hosted paper manager and AI research assistant that runs entirely on Docker Compose.

- Read, highlight and take Markdown notes on PDFs in the browser; share them in lab workspaces.
- PDFs are stored as plain files you can open from the host (`{Year}/{Identifier}_{SlugTitle}.pdf`).
- Metadata and BibTeX are filled in from arXiv, CrossRef and Semantic Scholar.
- A lightweight metadata API with personal access tokens, for CLIs, scripts and MCP servers.
- AI features (chat grounded in the paper, figure/equation explanations, hybrid search across the library) are planned (Phase 3).

The specification and the development status are in `AGENTS.md` (Japanese); design notes are in `specs/`.

## Setup

```bash
cp .env.example .env
# Replace every change-me-* value in .env with a generated secret:
openssl rand -hex 32   # once each for POSTGRES_PASSWORD / REDIS_PASSWORD / JWT_SECRET
```

```bash
docker compose up -d --build
docker compose ps        # every service should become healthy
```

Only one port is published to the host: the `biblioflow-gateway` (Caddy) on `BIND_ADDRESS:FRONTEND_PORT` (default http://127.0.0.1:3000). The database, Redis, the API and the frontend are not published. The gateway sends `/api/*` straight to the API and everything else to the frontend.

## Create a user

Self-registration is off by default (`ALLOW_REGISTRATION=false`). Create accounts from the CLI, in a terminal (the password prompt needs a TTY):

```bash
docker compose exec biblioflow-api python -m app.cli create-user --email you@example.com --admin
```

## Deployment modes

The application itself does not care how it is exposed; only a few `.env` values and the entry container (a compose profile) differ. The `Secure` cookie attribute and HSTS follow whether the request is https, so LAN (http) and a tunnel (https) can be used at the same time.

The gateway has three listeners:

| Port | Published | Purpose |
|---|---|---|
| `:8080` | Yes (`BIND_ADDRESS:FRONTEND_PORT`) | Direct clients. Any `X-Forwarded-*` they send is discarded and rewritten from the socket address. |
| `:8081` | No (`biblioflow-edge` network only) | cloudflared only. The client IP is taken from `CF-Connecting-IP` and nothing else. |
| `:8082` | No (`biblioflow-edge` network only) | tailscale only. The client IP is taken from `X-Forwarded-For` (overwritten by tailscale serve). `CF-Connecting-IP` is not trusted here, because tailscale serve passes it through. |

Request bodies are limited to `MAX_UPLOAD_MB` for `/api/v1/papers/upload` and 4 MiB for the rest of the API. Larger requests get a JSON 413 from the gateway before they reach the API (the API enforces the same limits).

### A. LAN only (http)

`.env`:

```bash
BIND_ADDRESS=0.0.0.0            # 127.0.0.1 for this machine only
FRONTEND_PORT=3000
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://192.168.1.10:3000   # every URL you open in a browser
```

Start with `docker compose up -d --build`. Nothing else to configure.

With Docker Desktop (macOS), every LAN client appears to come from the VM's gateway IP, so per-IP rate limits are shared by all devices. Login also has a per-email limit (20 attempts / 10 minutes), which covers this.

### B. Tailscale (zero trust)

No open ports; only devices on your tailnet can connect.

1. In the Tailscale admin console, enable MagicDNS and HTTPS Certificates (DNS page).
2. Create an auth key under Settings > Keys (one-off is fine). It is only needed for the first login; you can also run Tailscale on the host instead and skip the sidecar.
3. `.env`:

   ```bash
   BIND_ADDRESS=127.0.0.1
   TS_AUTHKEY=tskey-auth-...
   TS_HOSTNAME=biblioflow
   CORS_ALLOWED_ORIGINS=https://biblioflow.<tailnet>.ts.net,http://localhost:3000
   ```

4. Start: `docker compose --profile tailscale up -d --build`
5. Check `docker compose logs biblioflow-tailscale` and open https://biblioflow.<tailnet>.ts.net. After the first login you may clear `TS_AUTHKEY` (the node state stays in the `tailscale-state` volume).

It is reachable inside the tailnet only (Funnel is not used).

### C. Cloudflare Tunnel (public)

Reachable from anywhere without opening ports; the tunnel only connects outbound from the host. **If you expose it publicly, put Cloudflare Access in front of the hostname** (see below) and keep `ALLOW_REGISTRATION=false` (the default).

Set `BIND_ADDRESS=127.0.0.1` in `.env` unless you also want LAN access.

#### Set up from the CLI (recommended)

Create one API token (dashboard: My Profile > API Tokens > Create Token > Custom token) with:
`Account > Cloudflare Tunnel > Edit`, `Zone > DNS > Edit`, `Zone > Zone > Read` (limit the zone resources to your zone).

```bash
export CLOUDFLARE_API_TOKEN=<API token>          # not in .env: .env is passed to the app containers
scripts/cloudflare-tunnel.sh papers.example.com   # tunnel, route, DNS record and .env in one go
docker compose --profile cloudflare up -d --build
docker compose ps biblioflow-cloudflared         # healthy = connected
```

The script is idempotent. It creates or reuses a tunnel (default name `biblioflow`), routes `https://papers.example.com` to `http://biblioflow-gateway:8081`, creates the proxied CNAME, and writes `CLOUDFLARE_TUNNEL_TOKEN` plus the https origin in `CORS_ALLOWED_ORIGINS` into `.env`.

#### Set up from the dashboard

1. Zero Trust > Networks > Tunnels: create a tunnel and copy its token.
2. Add a public hostname with service `http://biblioflow-gateway:8081` (not `:8080`).
3. `.env`:

   ```bash
   CLOUDFLARE_TUNNEL_TOKEN=<token>
   CORS_ALLOWED_ORIGINS=https://papers.example.com,http://localhost:3000
   ```

4. `docker compose --profile cloudflare up -d --build`

#### Cloudflare Access (recommended)

Authentication at Cloudflare's edge, in front of the tunnel: before the BiblioFlow login, visitors must prove they own an allowed e-mail address (one-time PIN or any login method of your Zero Trust account).

Add these permissions to the API token: `Account > Access: Apps and Policies > Edit`, `Account > Access: Service Tokens > Edit`.

```bash
scripts/cloudflare-access.sh papers.example.com me@example.com,you@example.com > access-token.txt
docker compose --profile cloudflare up -d     # apply Access before starting the tunnel: no unprotected window
```

- The whole host is protected, `/api` included, with two policies: the listed e-mail addresses (allow) and a service token (Service Auth).
- The service token (`biblioflow-<hostname>`, valid for one year) is printed once on stdout as `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`. Keep it out of `.env`. If you lose it, `--rotate` issues a new secret (the old one stops working).
- The script is idempotent; re-run it to change the allowed addresses.
- **Clients that use a personal access token (CLI, scripts, MCP) must also send the service token headers**, or Access redirects them to its login page (302):

  ```bash
  curl -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
       -H "Authorization: Bearer bf_pat_..." https://papers.example.com/api/v1/papers/light
  ```
- Cloudflare's edge rejects a client-supplied `CF-Connecting-IP` with 403 and a forged `X-Forwarded-For` is ignored; the gateway log and the API see the real client IP.
- If Web Analytics (RUM) auto-injection is enabled for the zone, Cloudflare inserts a `static.cloudflareinsights.com` script into the HTML. Turn it off in Cloudflare if you do not want it.

## Production deployment

Use a dedicated clone for production and update it only with `scripts/deploy.sh`:

```bash
scripts/deploy.sh            # deploy origin/main
scripts/deploy.sh <commit>   # deploy a specific commit or tag (also how you roll back)
```

It refuses to run with local changes, takes a backup (`scripts/backup.sh`), checks out the ref, rebuilds, waits until every service is healthy and checks `/api/v1/readyz` through the gateway. Migrations run when the API starts; a migration is not undone by rolling back the code, so restore the backup taken by the deploy if you need to.

The compose profiles to start (for example `cloudflare`) are read from `COMPOSE_PROFILES` in `.env`.

### A second instance on the same machine

To run production next to a development checkout, give the production clone its own names and port in its `.env`:

```bash
BF_INSTANCE=biblioflow-prod          # container and network names: biblioflow-prod-*
COMPOSE_PROJECT_NAME=biblioflow-prod # separate volumes and images
FRONTEND_PORT=3090                   # must not clash with the other instance
COMPOSE_PROFILES=cloudflare
```

Each clone has its own `data/`, database volume, secrets and Cloudflare tunnel. Service names (`biblioflow-api`, ...) stay the same, so the commands in this README work in either directory.

## Logs

The gateway (Caddy) logs every request as JSON. `Cookie` and `Authorization` headers are replaced with `REDACTED`. `request.client_ip` is the resolved client IP (the real origin, also behind Cloudflare or Tailscale). Every container's log is rotated at 10 MB × 5 files.

```bash
# Failed logins (401) and rate limiting (429)
docker compose logs --no-color biblioflow-gateway | sed 's/^[^{]*//' \
  | jq -c 'select(.request) | select(.request.uri | startswith("/api/v1/auth/login")) | select(.status == 401 or .status == 429)
           | {ts, status, ip: .request.client_ip}'

# 4xx responses per IP
docker compose logs --no-color biblioflow-gateway | sed 's/^[^{]*//' \
  | jq -r 'select(.request) | select(.status >= 400 and .status < 500) | .request.client_ip' | sort | uniq -c | sort -rn | head
```

## Backup and restore

`scripts/backup.sh` writes a snapshot of the database (`pg_dump`) and of `data/library` to `BACKUP_DIR` (default `./backups`). Only files changed since the previous snapshot are copied; unchanged PDFs are hard links, so daily snapshots take little space. Snapshots beyond `BACKUP_KEEP` (default 14) are deleted.

```bash
BACKUP_DIR=/Volumes/nas/biblioflow scripts/backup.sh          # take a snapshot
scripts/restore.sh --check /Volumes/nas/biblioflow/<timestamp> # restore into a throwaway DB and verify (live data untouched)
scripts/restore.sh --yes   /Volumes/nas/biblioflow/<timestamp> # replace live data (backs up the current state first)
```

- Put `BACKUP_DIR` on another disk or machine; a backup on the same disk does not survive that disk.
- `.env` (secrets) is not included. Keep it somewhere safe yourself.
- Schedule it with cron or similar, e.g. `0 3 * * * cd ~/biblioflow && BACKUP_DIR=/mnt/nas/biblioflow scripts/backup.sh >> backups.log 2>&1`, and run `--check` weekly.

## Tests

Integration tests run against the `biblioflow_test` database and need an image built with `INSTALL_DEV=true`.

```bash
INSTALL_DEV=true docker compose build biblioflow-api
docker compose run --rm biblioflow-api pytest
```

## Directories

- `data/library/`: stored PDFs, `{Year}/{Identifier}_{SlugTitle}.pdf` (readable directly from the host)
- `data/watch/`: watch folder; PDFs dropped in `data/watch/<workspace-slug>/` are imported automatically
- `db/init/`: database initialization SQL (runs on first start only)

## Sponsoring

If BiblioFlow is useful to you, you can support its development through [GitHub Sponsors](https://github.com/sponsors/pathvector-studio).

## License

MIT
