# BiblioFlow

[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/pathvector-studio)

A self-hosted paper manager and AI research assistant that runs entirely on Docker Compose.

Website: https://biblioflow.pathvector.dev

> **Status: early preview (v0.3.0).** The paper manager (storage, metadata, reader, notes, workspaces, API, hybrid keyword + semantic search, MCP server), the in-app AI (chat with citations that jump into the PDF, explaining a selected figure), the browser extension, and export / import (BibTeX, RIS, CSL JSON, Zotero RDF) work today; see [Roadmap](#roadmap) for what is next. Changes per release are in [CHANGELOG.md](CHANGELOG.md); how releases are made is in [RELEASING.md](RELEASING.md).

- Read, highlight and take Markdown notes on PDFs in the browser; share them in lab workspaces.
- PDFs are stored as plain files you can open from the host (`{Year}/{Identifier}_{SlugTitle}.pdf`).
- Metadata and BibTeX are filled in from arXiv, CrossRef and Semantic Scholar.
- A lightweight metadata API with personal access tokens, for CLIs, scripts and MCP servers.
- Hybrid search over the full text of every paper (keywords, substrings and meaning-based matching), with page and position of each hit.
- An MCP server, so an MCP client you already use (Claude Code and others) can search and read your library with its own model.

The specification and the development status are in `AGENTS.md` (Japanese); design notes are in `specs/`. To work on BiblioFlow itself, start with [`LOCAL_DEV.md`](LOCAL_DEV.md) (what to prepare, where secrets live, tests).

## Setup

The installer ([`install.sh`](install.sh)) clones this repository to `~/biblioflow`, writes `.env` with generated secrets and starts the stack:

```bash
curl -sSL https://biblioflow.pathvector.dev/install.sh | bash
```

Options are environment variables: `BIBLIOFLOW_DIR` (directory), `BIBLIOFLOW_PORT` (default 3000), `BIBLIOFLOW_REF` (branch or tag; default: the latest release, `main` to try unreleased changes), `BIBLIOFLOW_NO_START=1` (prepare only), `BIBLIOFLOW_SEMANTIC_SEARCH=yes|no` (answers the question below without asking). On Linux the containers run with your user and group IDs so they can write to `data/`. It stops if the port is taken or another BiblioFlow on the machine uses the same compose project name; to run a second one, see [A second instance on the same machine](#a-second-instance-on-the-same-machine).

The installer asks one question: whether to download the model for semantic search (bge-m3, int8, about 0.6 GB, from this repository's GitHub Release, MIT; it runs on your machine and your papers' text is never sent anywhere). The default is no. Answering yes writes `EMBEDDING_PROVIDER=local` to `.env`, which is the consent. The question is read from the terminal even under `curl | bash`; with no terminal the answer is no unless `BIBLIOFLOW_SEMANTIC_SEARCH=yes` is set. You can change it any time (see [Embedding providers](#embedding-providers)).

### Updating

Read the release's **Upgrade notes** in [CHANGELOG.md](CHANGELOG.md), take a backup, then run the installer again (with the same `BIBLIOFLOW_DIR` if you changed it). It keeps `.env` and your data, checks out the latest release and rebuilds; migrations run when the API starts.

```bash
cd ~/biblioflow && scripts/backup.sh
curl -sSL https://biblioflow.pathvector.dev/install.sh | bash
```

To go back, restore that backup on the previous release (see [Backup and restore](#backup-and-restore)). A production clone is updated with `scripts/deploy.sh` instead, which takes the backup itself (see below).

Or by hand:

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
scripts/check-access.sh papers.example.com    # every path, /api included, must stop at Access
docker compose --profile cloudflare up -d     # apply Access before starting the tunnel: no unprotected window
scripts/check-access.sh papers.example.com    # and again once the tunnel serves the host
```

Move the service token from `access-token.txt` into your password manager and delete the file.

- The whole host is protected, `/api` included, with two policies: the listed e-mail addresses (allow) and a service token (Service Auth).
- The service token (`biblioflow-<hostname>`, valid for one year) is printed once on stdout as `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET`. Keep it out of `.env` (for example in a password manager). If you lose it, `--rotate` issues a new secret (the old one stops working).
- The script is idempotent; re-run it to change the allowed addresses.
- **Clients that use a personal access token (CLI, scripts, MCP) must also send the service token headers**, or Access redirects them to its login page (302):

  ```bash
  curl -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
       -H "Authorization: Bearer bf_pat_..." https://papers.example.com/api/v1/papers/light
  ```
- Cloudflare's edge rejects a client-supplied `CF-Connecting-IP` with 403 and a forged `X-Forwarded-For` is ignored; the gateway log and the API see the real client IP.
- If Web Analytics (RUM) auto-injection is enabled for the zone, Cloudflare inserts a `static.cloudflareinsights.com` script into the HTML. Turn it off in Cloudflare if you do not want it.

### Exporting references

`GET /api/v1/export?format=bibtex|ris|csljson` takes the same filters as `/api/v1/papers/light` (`workspace_id`, `collection_id`, `tag`, `status`, `year`, `q`) and returns a file download; a personal access token needs the `read:papers` scope. The library screen's Export menu uses the current filters.

```bash
curl -H "Authorization: Bearer bf_pat_..." -o refs.bib "https://papers.example.com/api/v1/export?format=bibtex&tag=networking"
```

#### Auto-export

Settings > Auto-export keeps a file up to date for LaTeX, Obsidian or any tool that reads a `.bib`, `.ris` or CSL `.json` file. Pick a format, a filename (letters, digits, `.`, `_`, `-`; the extension must match the format) and optional filters (collection, tag, status, year, search). The worker then writes `data/exports/<workspace-slug>/<filename>` and rewrites it within a minute or two of any matching paper being added, edited or deleted (it compares a cheap fingerprint every minute and writes through a temporary file and a rename, so readers never see a half-written file). The content is the same as the download from `/api/v1/export` with the same filters. Adding and deleting needs the editor role; deleting also removes the file, and a write failure is shown next to the entry.

Exports are regenerated from the database, so `scripts/backup.sh` does not copy `data/exports`; after a restore the worker rewrites the files within a minute. The directory is `EXPORT_DIR` (default `/data/exports`, mounted from `./data/exports`).

### Importing references

Add > "Import from a bibliography file" reads a BibTeX (`.bib`), RIS (`.ris`, including Mendeley Reference Manager exports), CSL JSON or Zotero RDF file of up to 4 MiB (the format is detected from the content) and adds the records to the current workspace as wishlist papers without a PDF. A preview shows how many records are new, match a paper you already have (same DOI, arXiv ID or identifier) and could not be read; nothing is saved until you confirm.

- Tags come from `keywords` / `KW` / `dc:subject`, Zotero collections become collections with the same names and nesting, and notes (`note` / `annote`, `N1`, Zotero notes) are appended to the paper's notes. A BibTeX record keeps its original entry text in the BibTeX tab.
- A matching paper keeps its title, authors and other values; only tags, collections and notes are added. Importing the same file again changes nothing. Records without a DOI or arXiv ID get a `BF-` identifier derived from the title, year and first author, which is what makes that possible.
- Nothing is looked up on the internet and no link in the file is opened, so the data is exactly what the file says. Records that cannot be read (no title, a broken entry) are listed and skipped; the others are imported. The whole file is one database transaction per import.
- Editors and owners only. The API is `POST /api/v1/imports/preview` and `POST /api/v1/imports` (multipart `file` and optional `workspace_id`; a token needs `write:papers`), limited to 20 previews and 5 imports per minute. File paths in the records (`file`, `L1`, Zotero attachments) are listed but not imported here; PDFs are not uploaded through the browser; see the next section for importing them from a server folder.

#### With PDFs, from a server folder

To move a library together with its PDFs (for example from Zotero), put the files on the server instead of uploading them: Add > "Import with PDFs from the server folder" reads `data/import/<workspace-slug>/` (the screen shows the exact folder; `IMPORT_DIR` in `.env`, default `/data/import`, mounted from `./data/import`). In Zotero use File > Export Library > Zotero RDF with "Export Files" ticked (or BibTeX with "Export Files"), and copy the whole output folder (the `.rdf` / `.bib` file and its `files/` folder) there. A BibTeX or RIS file whose `file` / `L1` fields point at PDFs under that folder works the same way. Relative paths are taken from the folder of the bibliography file; `file://` links and absolute paths are accepted only when they point inside the workspace folder.

- Pick the file from the list and start the import (editors and owners). It runs in the worker: the references are added exactly as above (matching on DOI, arXiv ID or identifier; tags, nested collections and notes included), and then the first PDF of each record is stored through the same checks as an upload (PDF header, size limit, isolated parser) and indexed. The screen shows progress, the counts, and the records that failed with the reason; a record whose PDF fails still keeps its metadata.
- A paper that already has a PDF keeps it. A matched wishlist paper gets the PDF attached. Running the same import again therefore creates nothing new and stores no PDF twice. One import runs per workspace at a time.
- Paths that leave the workspace folder (`..`, absolute paths elsewhere, symbolic links anywhere on the way, anything that is not a regular file) are refused and reported for that record only. The file you pick is chosen from the server listing and checked the same way.
- The API is `GET /api/v1/workspaces/{id}/import-folder` (listing), `POST /api/v1/workspaces/{id}/import-jobs` with `{"source": "<listed name>"}` (a token needs `write:papers`; 5 per minute) and `GET .../import-jobs[/{job_id}]` (`read:papers`).
- `data/import` is your input, not BiblioFlow's data: `scripts/backup.sh` does not copy it and nothing in it is deleted after the import. Keep the originals until you have checked the result, and remove them yourself afterwards.

## Production deployment

Use a dedicated clone for production and update it only with `scripts/deploy.sh`:

```bash
scripts/deploy.sh            # deploy origin/main
scripts/deploy.sh <commit>   # deploy a specific commit or tag (also how you roll back)
```

It refuses to run with local changes, takes a backup (`scripts/backup.sh`), checks out the ref, rebuilds, waits until every service is healthy and checks `/api/v1/readyz` through the gateway. Migrations run when the API starts; a migration is not undone by rolling back the code, so restore the backup taken by the deploy if you need to.

The compose profiles to start (for example `cloudflare`) are read from `COMPOSE_PROFILES` in `.env`.

Semantic search is off unless `.env` says `EMBEDDING_PROVIDER=local` (see [Search, embeddings and MCP](#search-embeddings-and-mcp)); an `.env` that predates the setting gets the default, `none`. Where it is on, a deploy needs no manual re-indexing: the worker fills in missing embeddings by itself.

### Upgrading from Redis to Valkey (one time)

The job queue and session store moved from Redis to [Valkey](https://valkey.io) (BSD-3). Redis 7.4+ is RSALv2/SSPLv1. Valkey cannot read Redis 7.4 data files, so it starts with a new volume. After the deploy, copy over the revoked sessions (logged-out cookies), or they are accepted again until they expire:

```bash
scripts/deploy.sh
scripts/migrate-redis-to-valkey.sh   # prints how to delete the old volume afterwards
```

Rate-limit counters, pending jobs and lookup caches are not copied. They are rebuilt on their own.

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
- `scripts/deploy.sh` also takes a backup (to `./backups` unless `BACKUP_DIR` is set) before every deployment.

### Scheduled backups

Run the backup daily and verify the latest snapshot weekly. With cron (`crontab -e`), replacing the paths with yours:

```cron
# daily 03:00: snapshot
0 3 * * *  cd /path/to/biblioflow && PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin BACKUP_DIR=/mnt/nas/biblioflow scripts/backup.sh >> /mnt/nas/biblioflow.log 2>&1
# Sunday 03:30: restore the newest snapshot into a throwaway database and compare
30 3 * * 0 cd /path/to/biblioflow && PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin scripts/restore.sh --check "$(ls -1d /mnt/nas/biblioflow/2*Z | tail -n 1)" >> /mnt/nas/biblioflow.log 2>&1
```

- cron runs with a minimal `PATH`; it must include the directory of the `docker` CLI (`/usr/local/bin` for Docker Desktop and OrbStack on macOS, `/usr/bin` on most Linux distributions).
- cron does not expand `~`; use absolute paths.
- The machine must be awake at that time (on macOS, disable sleep or use `pmset repeat wake` before the job).
- Check the log now and then: a failed backup only shows up there.

## Search, embeddings and MCP

When a PDF is imported, the worker extracts its text page by page (with pypdf, in a resource-limited child process) and stores passages together with their position on the page. A second job then computes one embedding per passage. This powers `GET /api/v1/search?q=...&mode=hybrid|keyword|semantic` and reading by page (`GET /api/v1/papers/{id}/chunks`).

`hybrid` (the default) merges three ranked lists with reciprocal rank fusion: full-text words, substring matches (this is what finds Japanese text) and nearest embeddings. Each hit says which lists found it in `matched`. A paper is searchable by keyword as soon as its text is extracted, and its `index_status` becomes `ready` then; embeddings follow separately (`GET /api/v1/search/status` reports the provider, the model state and how many of your chunks have a vector). If semantic search is off, the model is not there yet, or the embedding service is down or too slow (2 seconds for the query), the search still answers with the keyword results and sends the response header `X-Search-Degraded: semantic-unavailable`. Nearest embeddings below a cosine similarity of `SEARCH_MIN_SIMILARITY` (default 0.45) are dropped, so an unrelated query is not padded with semantic matches; set it to 0 to keep them all.

### Embedding providers

Set `EMBEDDING_PROVIDER` in `.env` (the default is `none`; choosing `local` is the consent to download the model) and run `docker compose up -d`:

| Provider | What it does |
|---|---|
| `local` | [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3) on ONNX Runtime (CPU) in the `biblioflow-embedder` container. Nothing leaves your machine. |
| `voyage` | Voyage AI `voyage-3.5` (1024 dimensions). Needs `EMBEDDING_API_KEY`. |
| `openai` | OpenAI `text-embedding-3-large` (1024 dimensions), or any OpenAI-compatible server through `EMBEDDING_BASE_URL` and `EMBEDDING_MODEL` (Ollama, vLLM, TEI). On a Mac, Ollama runs bge-m3 on the GPU, which is much faster than Docker. |
| `none` (default) | No embeddings, and nothing is downloaded. Search uses keywords and substrings only. For small machines, or until you decide to turn semantic search on. |

**Privacy:** with `voyage` or `openai` the text of every passage of every paper you import, and every search query, is sent to that service. Use `local` (or an OpenAI-compatible server you run yourself) for unpublished or confidential papers. With `local` the `biblioflow-embedder` container has no internet access, no database access and none of your secrets.

The vectors are tagged with the model that made them (`paper_chunks.embedding_model`), and search compares only vectors of the current model. After you switch the provider, search is keyword-only until the worker has re-embedded (see below).

### The local model

With `EMBEDDING_PROVIDER=local`, the `biblioflow-model-init` container (it stays idle afterwards) downloads the int8 model (`model_int8.onnx`, 569 MB, and `tokenizer.json`) from this repository's GitHub Release [`model-bge-m3-int8-5617a9f-1`](https://github.com/pathvector-studio/biblioflow/releases/tag/model-bge-m3-int8-5617a9f-1), checks the size and sha256 pinned in the source, stores them in the `models` volume and, last, writes a `READY` marker. Nothing is converted on your machine: a CI workflow (`.github/workflows/model-artifact.yml`) quantizes the official `BAAI/bge-m3` once and attests the result; `scripts/build-model-artifact.py` reproduces it byte for byte. `EMBEDDING_LOCAL_PRECISION=fp32` instead downloads the four official files from Hugging Face (about 2.3 GB; set `EMBEDDER_MEM_LIMIT=4g`).

Nothing waits for the download. `docker compose up -d --wait` returns in seconds; the `biblioflow-embedder` container starts without a model, reports `absent` on its health endpoint, and checks every 10 seconds for `READY`. It then re-verifies the files' sha256 (the volume is shared, so a marker alone is not trusted), loads the model in the background (`loading`, then `ready`) and answers `503 model not ready` until then. Meanwhile search works with keywords only. About every 10 minutes the worker queues `embed_paper` for papers that have chunks without a vector from the current model, once the embedder is ready, so existing papers are indexed without any command. The `models` volume is not backed up because it can be downloaded again.

Measured (Docker on an 8-CPU machine): the download and verification take about 15 seconds on a fast connection with model-init at under 30 MiB of memory (limit `MODEL_INIT_MEM_LIMIT`, default 512m; the old local quantization needed about 9 GiB), and the embedder is ready about 15 seconds after the file is complete. The loaded embedder uses about 1 GiB (`EMBEDDER_MEM_LIMIT`, default 3g) and up to 4 CPUs (`EMBEDDER_CPUS`). Plan for about 1 GB of disk for int8 (3 GB for fp32). About 4 passages per second with int8 (an average paper takes 15 to 30 seconds; 1,000 papers take several hours the first time), 10 ms per search query. fp32 is about three times slower; the int8 vectors agree with fp32 on about 90% of nearest neighbours.

### After switching the provider

Vectors are tagged with the model that made them, so after a switch the worker re-embeds everything on its own (keyword-only search in the meantime). You no longer have to run anything. To queue it immediately instead of waiting up to 10 minutes (safe to repeat; `--missing` skips papers that are up to date):

```bash
docker compose exec biblioflow-api python -m app.cli reindex --all --embeddings-only --missing
```

Without `--embeddings-only`, `reindex` extracts the text again (`--all`, or `--paper <paper-id>` for one paper).

`mcp/` contains `biblioflow-mcp`, an MCP server (stdio) for Claude Code and other MCP clients. It is a thin client of the HTTP API: it needs a personal access token (Settings > API tokens; `read:papers`, plus `write:papers` for `add_note`) and no LLM key. Tools: `search_library` (with `mode`; results show which lists matched and whether semantic search was unavailable), `list_papers`, `get_paper`, `read_pages`, `list_annotations`, `add_note`. Text that comes from papers is wrapped in `<paper_context>` tags, which the client is told to treat as data, not instructions.

```bash
claude mcp add biblioflow \
  -e BIBLIOFLOW_URL=https://papers.example.com \
  -e BIBLIOFLOW_TOKEN=bf_pat_... \
  -- uvx --from "git+https://github.com/pathvector-studio/biblioflow#subdirectory=mcp" biblioflow-mcp
```

Behind Cloudflare Access, also pass the service token: `-e CF_ACCESS_CLIENT_ID=... -e CF_ACCESS_CLIENT_SECRET=...`. To develop it: `uv run --directory mcp pytest`.

## AI chat and figure explanations

BiblioFlow can answer questions about a paper or the whole library, and explain a figure, equation or table you select on a PDF page, with a model you choose. It is **off by default** (`LLM_PROVIDER=none`): nothing is sent anywhere, and the chat endpoints answer `503 llm_disabled`. The installer leaves it off, because most providers need an API key.

- `POST /api/v1/chat` with `paper_id`: a question about one paper. Without `paper_id`: a question across every workspace you belong to (or one, with `workspace_id`). The answer streams as server-sent events (`meta`, `delta`, `done`, or `error`). Citations are numbered markers such as `[1]` that the server checks against the passages it actually sent; each comes back with the paper, page and position in the PDF and a label such as `p.4, Sec 3.2`.
- `POST /api/v1/papers/{id}/explain-region`: a PNG or JPEG cut from a page (up to 1.5 MiB) and the rectangle it came from. The server checks the image, scales it to at most 2048 px, re-encodes it as PNG (dropping metadata) and sends it with the text of that page. It needs a vision-capable model.
- `GET /api/v1/chat/status`: whether chat is on, the provider and model, whether images are supported and how many requests you have left today. `GET /api/v1/chat/conversations` lists your conversations; only you can read them, and they are deleted `CHAT_RETENTION_DAYS` (default 90) after the last message. Images are never stored.
- API tokens need the `use:ai` scope for chat and explanations, so a read-only token that leaks cannot spend your LLM budget.

### Providers

Set these in `.env` and run `docker compose up -d`:

| `LLM_PROVIDER` | `LLM_MODEL` (example) | Also needs |
|---|---|---|
| `anthropic` | `claude-sonnet-5-5` | `LLM_API_KEY` |
| `openai` | `gpt-4o-mini` | `LLM_API_KEY` |
| `gemini` | `gemini-2.5-flash` | `LLM_API_KEY` |
| `ollama` | `llama3.2` (a vision model such as `llama3.2-vision` for explanations) | `LLM_API_BASE`, e.g. `http://host.docker.internal:11434` |
| `openai_compatible` | the server's model name (vLLM, LM Studio, ...) | `LLM_API_BASE`, and `LLM_API_KEY` if the server wants one |

If a provider is chosen but its model, key or `LLM_API_BASE` is missing, the API still starts, logs `AI chat is disabled: ...` and keeps chat off. `VISION_MODEL` sets a separate model for explanations; when it is empty, `LLM_MODEL` is used if LiteLLM says it accepts images. Calls go through the [LiteLLM](https://github.com/BerriAI/litellm) SDK (not its proxy). LiteLLM's telemetry and callbacks are turned off, and provider errors are reduced to a fixed message so that a key or URL never ends up in a response or a log.

### What is sent to the provider

| Sent | Not sent |
|---|---|
| Your question | The PDF file |
| Up to `LLM_MAX_CONTEXT_CHUNKS` (default 8) passages found by search, each cut to 1,500 characters | Anything from workspaces you cannot access |
| The title, authors and year of the papers those passages come from | Your e-mail address, user ID or API tokens |
| The last 6 exchanges of the conversation (at most 8,000 characters) | Other users' conversations |
| For explanations: the selected image and the text of that page | |

With `ollama` (or an OpenAI-compatible server you run yourself) nothing leaves your network. Logs record only counts, the model, token numbers and timings, never the question, the answer, the passages or the image.

Text from papers is passed inside `<paper_context>` tags, and the system prompt tells the model to treat it as data, never as instructions. A paper with hidden text such as "ignore the previous instructions" can at worst produce a wrong answer: the model has no tools, the server drops citations that point at nothing, and Markdown images (a way to leak data through a URL) are removed from answers.

### Limits

Per user: 10 chat requests and 5 explanations per minute (and 30 per minute per IP address), at most 2 answers streaming at once, and per day `LLM_DAILY_REQUESTS_PER_USER` requests (default 200) and `LLM_DAILY_TOKENS_PER_USER` tokens (default 200,000). Each answer is capped at `LLM_MAX_OUTPUT_TOKENS` (default 1,024). If search finds nothing, the model is not called at all.

## Browser extension

`extension/` holds a small extension (Chrome and Firefox, plain JavaScript, no dependencies) that saves the paper you are reading with one click, the way Zotero Connector does. It reads the page's meta tags (`citation_*`, `dc.*`, arXiv / DOI / ScienceDirect URLs) only when you press the toolbar button, fetches the PDF with your own browser session (so subscriptions and cookies apply), and sends it to your BiblioFlow server with `POST /api/v1/papers/upload`. If the PDF cannot be fetched, it saves the paper to your wishlist and says so. It never imitates a browser to get around a site's bot protection.

```bash
node extension/build.mjs     # writes extension/dist/{chrome,firefox}/ and two zips
```

- **Chrome**: open `chrome://extensions`, turn on Developer mode, "Load unpacked", pick `extension/dist/chrome`.
- **Firefox**: open `about:debugging#/runtime/this-firefox`, "Load Temporary Add-on", pick `extension/dist/firefox/manifest.json` (Firefox 128 or later).
- **Settings**: in BiblioFlow, create an API token under Settings with the scopes `read:papers` and `write:papers`. Open the extension's settings, enter your BiblioFlow URL and the token, and press "Test connection" (the browser asks once for permission to contact that one server). The token is kept in the extension's local storage and is never given to web pages.
- **Cloudflare Access**: if BiblioFlow is behind Access, sign in to Access in the same browser; its cookie is sent automatically. A service token (`CF-Access-Client-Id` / `-Secret`) can be entered instead.

The extension's only permissions are `activeTab`, `scripting` and `storage`, plus the host permission you grant for your own server. Tests: `node --test 'extension/test/*.test.js'`.

## Tests

Integration tests run against the `biblioflow_test` database and need an image built with `INSTALL_DEV=true`.

```bash
INSTALL_DEV=true docker compose build biblioflow-api
docker compose run --rm biblioflow-api pytest
```

## Directories

- `data/library/`: stored PDFs, `{Year}/{Identifier}_{SlugTitle}.pdf` (readable directly from the host)
- `data/watch/`: watch folder; PDFs dropped in `data/watch/<workspace-slug>/` are imported automatically
- `data/import/`: files to import with their PDFs, `<workspace-slug>/` (your input; not part of the backup)
- `data/exports/`: auto-export files, `<workspace-slug>/<filename>` (regenerated by the worker; not part of the backup)
- `db/init/`: database initialization SQL (runs on first start only)

## Roadmap

Not implemented yet (details in `specs/roadmap.md`; the conditions for 1.0 are in [RELEASING.md](RELEASING.md#versioning)):

- **Citation styles**: a bibliography in a CSL style (APA, IEEE, …) from a collection, tag or selection.
- **Translation** of selected text in the PDF.
- **More of what Zotero does**: books and other non-PDF items, duplicate detection and merging, open-access PDFs (Unpaywall), trash and restore, saved searches, notes and annotations exported as Markdown.
- **Published clients**: the browser extension in the Chrome Web Store and on AMO, and `biblioflow-mcp` on PyPI. Until then, load the extension from `extension/` and run the MCP server from `mcp/`.

## Security

Please report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md); do not open a public issue. Only the gateway is published to the host; the database, Valkey, the API and the embedder stay on an internal network, and the containers run as non-root. Uploaded PDFs and bibliography files are parsed in resource-limited child processes, outgoing requests refuse private addresses, and text from papers is passed to language models as data inside `<paper_context>`, never as instructions.

## Sponsoring

If BiblioFlow is useful to you, you can support its development through [GitHub Sponsors](https://github.com/sponsors/pathvector-studio).

## License

MIT. Third-party components in the images and their licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
