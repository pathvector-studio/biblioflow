# Changelog

All notable changes to BiblioFlow are written here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/) as defined for BiblioFlow in [RELEASING.md](RELEASING.md#versioning).

Each pull request adds its lines under `[Unreleased]` (CI checks it; see RELEASING.md). Sections: Added, Changed, Deprecated, Removed, Fixed, Security, and **Upgrade notes** for anything an operator has to do (new `.env` keys, manual steps).

## [Unreleased]

### Fixed
- The installer's closing message no longer says AI chat is on the roadmap (it says how to turn it on when it is off), and it no longer tells you to create your first user when it updated an existing install.

## [0.3.0] - 2026-11-12

### Added
- Import references from a BibTeX, RIS (Mendeley Reference Manager included), CSL JSON or Zotero RDF file (up to 4 MiB) on the Add screen, or with `POST /api/v1/imports/preview` and `POST /api/v1/imports`: a preview of new, already-present and unreadable records, then wishlist papers with their tags, collections (Zotero nesting kept) and notes. Existing papers are never overwritten, and importing a file twice adds nothing. Nothing is fetched from the internet.
- Export the library (or any filtered view: workspace, collection, tag, status, year, search) as BibTeX, RIS or CSL JSON from the library screen, or with `GET /api/v1/export?format=bibtex|ris|csljson` and a token with `read:papers`. BibTeX citekeys match the ones shown in the app and are unique within the file.
- Browser extension (`extension/`, Chrome and Firefox): save the paper you are reading with one click. It uses your own browser session to fetch the PDF and sends it with its DOI / arXiv ID to your server with an API token; if the PDF is not available, the paper goes to your wishlist. It reads a page only when you press the button. See the README's "Browser extension" section.
- `read_pages` and `get_paper` (MCP) and the chunks and paper detail APIs return the PDF's SHA-256, so citations can be pinned to the exact file.
- AI chat settings (`LLM_PROVIDER`, default `none` = off; `LLM_MODEL`, `LLM_API_KEY`, `LLM_API_BASE`, daily limits) and `GET /api/v1/chat/status`. Nothing is sent to a model until you choose a provider. The README's "AI chat and figure explanations" section lists the provider settings and exactly what is sent.
- Stored AI conversations: `GET /api/v1/chat/conversations`, `GET /api/v1/chat/conversations/{id}` and `DELETE`. Only the author can read a conversation; it is deleted after `CHAT_RETENTION_DAYS` (default 90, `0` keeps it forever).
- `POST /api/v1/chat`: ask one paper a question and get a streamed answer (server-sent events) with citations that point at the page and position in the PDF. API tokens need the new `use:ai` scope; there are per-minute, per-day and concurrency limits.
- `POST /api/v1/chat` without `paper_id` asks the whole library (hybrid search over every workspace you belong to, or one with `workspace_id`); citations carry the paper they come from. Library-wide conversations are listed with the others (`paper_id` is null).
- `POST /api/v1/papers/{id}/explain-region`: send a rectangle cut from a PDF page (figure, equation, table) to a vision model and get a streamed explanation in the context of that page. The image is checked and re-encoded on the server (PNG/JPEG up to 1.5 MiB, long side scaled to 2048 px, metadata dropped) and is never stored; it needs a vision-capable model (`GET /api/v1/chat/status` shows `vision_available`) and the `use:ai` scope for tokens, with a 5 per minute limit.
- The paper screen has an AI tab: ask about the paper and watch the answer stream in (with a Stop button). Citations appear as badges like `p.4, Sec 3.2`; click one to jump to that page and highlight the cited passage in the PDF. Your earlier conversations for the paper can be reopened, continued and deleted.
- An "AI" page in the top navigation asks the whole library (all workspaces or one): citations show the paper title, and clicking one opens that paper at the cited page with the passage highlighted. Library-wide conversations can be reopened, continued and deleted there.
- Select an area of the PDF (figure, table, equation) and press "Explain with AI" to get a streamed explanation in the AI tab, with an optional question. Viewers who cannot annotate can use it too; nothing is saved to the paper. It only appears when the model supports images.
- Auto-export (Settings > Auto-export): register a format, a filename and filters per workspace and the worker keeps `data/exports/<workspace-slug>/<filename>` (BibTeX, RIS or CSL JSON) up to date, rewriting it within a minute or two of any paper being added, edited or deleted. Editors can add and delete; the API is `GET/POST /api/v1/workspaces/{id}/auto-exports` and `DELETE .../{auto_export_id}`.
- Import with PDFs from a server folder (Add > "Import with PDFs from the server folder"): put a Zotero "Export Files" output (RDF or BibTeX + `files/`) or a BibTeX / RIS file with `file` fields in `data/import/<workspace-slug>/`, pick it on screen, and a worker job adds the references and stores and indexes the PDFs, with progress and per-record failures shown. Paths outside the folder (`..`, absolute paths elsewhere, symlinks) are refused for that record only, and importing again never duplicates a paper or replaces a PDF. API: `GET /api/v1/workspaces/{id}/import-folder`, `POST` and `GET /api/v1/workspaces/{id}/import-jobs`.

### Upgrade notes
- Auto-export needs a new `./data/exports` directory writable by the container user (`APP_UID`). The installer creates it; on an existing install run `mkdir -p data/exports` (and `chown` it like `data/library` if the host user differs) before `docker compose up -d`. `EXPORT_DIR` (default `/data/exports`) is optional in `.env`.
- Importing from a server folder needs a new `./data/import` directory, created and owned like `data/exports` (`mkdir -p data/import`, `chown` to `APP_UID` if the host user differs). `IMPORT_DIR` (default `/data/import`) is optional in `.env`. Its contents are not part of the backup.

### Fixed
- The paper screen decides who may edit (notes, bibliography, annotations, PDF upload) by the paper's own workspace, not by the workspace picked in the header. Before, a viewer of a shared workspace saw editing tools on its papers when their own workspace was selected (the server still refused the changes), and the bibliography tab offered the header workspace's tags and collections.
- An old `.env` that still has `LLM_PROVIDER=openai` without `LLM_API_KEY` (the former `.env.example` placeholder) no longer stops the API from starting: AI chat stays off and a warning is logged naming the missing setting.
- Browser extension: IEEE Xplore pages (which have no `citation_*` tags) now give the DOI, title, authors and PDF link, and ACM Digital Library pages give the PDF link and correctly split author names. Checked against the live pages of arXiv, PLOS, Nature, SpringerLink, Wiley, ACM and IEEE.
- BibTeX export: DOIs, URLs and arXiv ids are written as they are (`10.1007/978-3-030-58452-8_13`, not `…\_13`), a title with unbalanced braces no longer breaks the entry, and an institution or a name containing "and" stays one author (`{World Health Organization}`). Checked with `biber --tool` and `bibtex`; the CSL JSON export passes the official CSL schema.
- An imported BibTeX entry that uses `@string` abbreviations keeps those definitions, so exporting it again no longer loses the journal or book title.

### Security
- Imported bibliography files (BibTeX, RIS, CSL JSON, Zotero RDF) are now read in a separate, resource-limited process (60 s for an upload, 10 min for a file in the import folder, 1 GiB of memory) and one file at a time per user. Before, a small crafted file (a Zotero RDF with collections listed under many parents, or a BibTeX file with thousands of `@string` lines) could keep the server busy indefinitely. This only affected unreleased code (file import was added after 0.2.0).
- Browser extension: a stored API token or Cloudflare Access secret is no longer reused after you change the server URL in the settings; type it again for the new server.
- AI answers no longer show clickable external links (the URL is shown as text), so text hidden in a paper cannot get the model to build a link that sends your question elsewhere. Notes no longer load remote images automatically (they appear as a link), so an image added by another workspace member or through MCP cannot tell an outside server when you open the note.

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

[Unreleased]: https://github.com/pathvector-studio/biblioflow/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/pathvector-studio/biblioflow/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/pathvector-studio/biblioflow/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/pathvector-studio/biblioflow/releases/tag/v0.1.0
