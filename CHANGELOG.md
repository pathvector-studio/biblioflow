# Changelog

All notable changes to BiblioFlow are written here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/) as defined for BiblioFlow in [RELEASING.md](RELEASING.md#versioning).

Each pull request adds its lines under `[Unreleased]` (CI checks it; see RELEASING.md). Sections: Added, Changed, Deprecated, Removed, Fixed, Security, and **Upgrade notes** for anything an operator has to do (new `.env` keys, manual steps).

## [Unreleased]

### Added
- `read_pages` and `get_paper` (MCP) and the chunks and paper detail APIs return the PDF's SHA-256, so citations can be pinned to the exact file.
- AI chat settings (`LLM_PROVIDER`, default `none` = off; `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, daily limits) and `GET /api/v1/chat/status`. Nothing is sent to a model until you choose a provider. The README's "AI chat and figure explanations" section lists the provider settings and exactly what is sent.
- Stored AI conversations: `GET /api/v1/chat/conversations`, `GET /api/v1/chat/conversations/{id}` and `DELETE`. Only the author can read a conversation; it is deleted after `CHAT_RETENTION_DAYS` (default 90, `0` keeps it forever).
- `POST /api/v1/chat`: ask one paper a question and get a streamed answer (server-sent events) with citations that point at the page and position in the PDF. API tokens need the new `use:ai` scope; there are per-minute, per-day and concurrency limits.
- `POST /api/v1/chat` without `paper_id` asks the whole library (hybrid search over every workspace you belong to, or one with `workspace_id`); citations carry the paper they come from. Library-wide conversations are listed with the others (`paper_id` is null).
- `POST /api/v1/papers/{id}/explain-region`: send a rectangle cut from a PDF page (figure, equation, table) to a vision model and get a streamed explanation in the context of that page. The image is checked and re-encoded on the server (PNG/JPEG up to 1.5 MiB, long side scaled to 2048 px, metadata dropped) and is never stored; it needs a vision-capable model (`GET /api/v1/chat/status` shows `vision_available`) and the `use:ai` scope for tokens, with a 5 per minute limit.


### Fixed
- An old `.env` that still has `LLM_PROVIDER=openai` without `LLM_API_KEY` (the former `.env.example` placeholder) no longer stops the API from starting: AI chat stays off and a warning is logged naming the missing setting.

## [0.2.0] - 2026-11-07

### Added
- Hybrid search: local bge-m3 embeddings (opt-in) combined with keyword search (`GET /api/v1/search?mode=hybrid|keyword|semantic`, `GET /api/v1/search/status`); the MCP `search_library` tool takes `mode`.
- The int8 bge-m3 model is downloaded prebuilt from this repository's GitHub Release, verified by size and sha256, in the background; search stays keyword-only until it is ready.
- The installer asks whether to enable semantic search (default no) and installs the latest release tag by default (`BIBLIOFLOW_REF=main` for unreleased changes).
- `scripts/version.py`: `VERSION` is the one place the version is set; CI checks every copy and the release tag.

### Changed
- README: how to update an installer install (backup, then run the installer again).
- Secrets and local notes for development live outside the checkout (`scripts/dev-setup.sh`, `LOCAL_DEV.md`).
- CI checks every commit on main for local-environment details and runs gitleaks on the whole history.

### Upgrade notes
- New `.env` keys with defaults: `EMBEDDING_PROVIDER` (default `none`; set `local` to download the model, which is the consent), `EMBEDDING_LOCAL_PRECISION`, `EMBEDDING_QUERY_TIMEOUT`, `SEARCH_MIN_SIMILARITY`, `MODEL_INIT_MEM_LIMIT`, `EMBEDDER_MEM_LIMIT`. See `.env.example`.
- Two new services, `biblioflow-embedder` and `biblioflow-model-init`. Migrations run when the API starts.
- Updating from 0.1.0 keeps semantic search off (keyword search only). To turn it on, set `EMBEDDING_PROVIDER=local` in `.env` and run `docker compose up -d`; existing papers are embedded in the background.
- How to update: [README, Updating](https://github.com/pathvector-studio/biblioflow#updating).

## [0.1.0] - 2026-11-06

First public preview.

### Added
- PDFs stored as plain files at `{Year}/{Identifier}_{SlugTitle}.pdf` (`FILENAME_TEMPLATE`), shared between workspaces when identical.
- Upload, Watch Folder import and (disabled by default) URL import, all through one ingest pipeline; metadata from arXiv, DOI (CrossRef), ScienceDirect PII and title search; BibTeX; a wishlist for papers without a PDF.
- Lightweight metadata API (`/api/v1/papers/light`, `metadata-only`, `lookup`, `upload`), personal access tokens with scopes and expiry.
- Web UI: library with filters, PDF reader with highlights, area annotations and Markdown notes, settings (tokens, workspaces).
- Workspaces shared by invitation, with roles; private data returns 404 to others.
- Full-text keyword search with page and position, and the `biblioflow-mcp` server.
- Deployment: Caddy gateway as the only published port, Cloudflare Tunnel (with Access) and Tailscale profiles, backup and restore scripts, `scripts/deploy.sh`, `install.sh`.

### Security
- Untrusted PDFs are parsed in a resource-limited child process; SSRF guard on outgoing requests; Origin check for cookie-authenticated writes; rate limits on login; containers run as non-root.

[Unreleased]: https://github.com/pathvector-studio/biblioflow/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/pathvector-studio/biblioflow/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/pathvector-studio/biblioflow/releases/tag/v0.1.0
