# BiblioFlow Phase 2: 軽量メタデータAPI & ファイル取り込みパイプライン

## Context
Phase 1（インフラ、DB、認証/RBAC、パスサニタイザ）は `main` にマージ済み（c504d73）。Phase 2 では AGENTS.md の §3.2（4つの API）、§3.1（Watch Folder）、§4.3（SSRF 対策）を実装する。これで論文を実際に登録・保存・取得できるようになる。
ユーザーと決めた事項:
- **Watch Folder は `/data/watch/{workspace_slug}/*.pdf` でワークスペースごとに振り分ける**
- **同じ identifier の実ファイルは複数ワークスペースで 1 つを共有する**
- 追加範囲: **論文詳細/更新、PDF 配信、URL からの PDF 取り込み**（削除は Phase 2 に含めない）

承認後、この計画を `specs/phase2.md` にコピーし、`main` から `feat/phase2-ingest` ブランチを切る。

## 追加依存（pyproject / uv.lock）
- `httpx`（dev から本体の依存へ移す）、`pypdf`（BSD。ページ数・メタデータ・先頭ページのテキスト抽出に使う）、`defusedxml`（arXiv Atom を XXE 安全にパースする）、`python-multipart`
- dev: 追加なし（外部 API は `httpx.MockTransport` で差し替える）
- メモ: PyMuPDF は AGPL で、リポジトリの MIT と相性が悪い。Phase 3 のパーサ選定で改めて扱う（Phase 2 では使わない）

## 設定追加（config.py / .env.example）
`CROSSREF_MAILTO`（polite pool 用。任意）、`SEMANTIC_SCHOLAR_API_KEY`（任意）、`EXTERNAL_HTTP_TIMEOUT=10`、`WATCH_POLL_SECONDS=10`、`URL_IMPORT_ENABLED=true`

## 新規・変更ファイル
```
backend/app/
├── core/net.py                    # SSRF: IP検証 + SafeNetworkBackend + safe_client()
├── schemas/metadata.py            # PaperMetadata, LookupResult（Agent A/B の契約）
├── schemas/paper.py               # PaperLight, PaperDetail, PaperPatch, MetadataOnlyIn, ImportUrlIn
├── services/metadata/
│   ├── identifiers.py             # 入力の正規化（arXiv/DOI/URL/タイトル判定）
│   ├── arxiv.py  crossref.py  semantic_scholar.py   # 各クライアント（safe_client 経由）
│   ├── resolver.py                # MetadataResolver: 判定→各API→マージ→Redis キャッシュ 24h
│   └── bibtex.py                  # citekey 生成、BibTeX 生成（LaTeX エスケープ）
├── services/pdf.py                # magic/MIME 検証、pypdf で page_count・タイトル・DOI/arXiv を抽出
├── services/storage/files.py      # .incoming へのストリーム保存(sha256) → 共有配置
├── services/storage/paths.py      # +with_collision_index(rel, n): "x.pdf" → "x_2.pdf"
├── services/ingest.py             # upload / watch / import-url 共通の取り込み処理
├── api/v1/papers.py               # 全エンドポイント
├── workers/tasks.py               # process_paper, scan_watch_folder
└── workers/settings.py            # functions/cron_jobs に登録
backend/alembic/versions/0002_citekey_unique.py   # UNIQUE(workspace_id, citekey)
```

## 契約（schemas/metadata.py）: 並列実装の境界
```python
class PaperMetadata(BaseModel):
    identifier: str; identifier_type: IdentifierType  # arxiv > doi > internal
    doi: str | None; arxiv_id: str | None             # arxiv_id はバージョンを除いた形
    title: str; authors: list[Author]                 # Author{family, given | None}
    year: int | None; venue: str | None; abstract: str | None
    source: Literal["arxiv","crossref","semantic_scholar","pdf"]
class LookupResult(BaseModel): query: str; kind: Literal["arxiv","doi","title"]; results: list[LookupItem]  # LookupItem = PaperMetadata + bibtex
class MetadataResolver(Protocol):
    async def resolve(self, query: str, *, limit: int = 5) -> LookupResult: ...
```
API と ingest は FastAPI の dependency `get_resolver()` で受け取る。テストでは fake に差し替える。

## SSRF 対策（core/net.py）
- `check_ip(ip)`: IPv4-mapped IPv6 は IPv4 に戻してから判定する。`not ip.is_global`、multicast、reserved、unspecified、`100.64.0.0/10`（CGNAT/Tailscale）、`169.254.0.0/16`、`fc00::/7`、`fe80::/10` をすべて拒否する。
- `SafeNetworkBackend(httpcore.AsyncNetworkBackend)`: `connect_tcp` の中で `getaddrinfo` し、**全アドレス**を検証してから、検証済みの IP に直接接続する。DNS rebinding 対策として、検証と接続の間に再解決しない。TLS の SNI と証明書検証は元の hostname のまま。
- `safe_client(allowed_hosts: set[str] | None)`: 上の backend を持つ `httpcore.AsyncConnectionPool` を包んだ transport を使う。設定は `trust_env=False`（プロキシ経由の迂回を防ぐ）と `follow_redirects=False`。リダイレクトは自前で最大 3 回までたどり、毎回 scheme(http/https)・port(80/443)・allowlist を再検証する。
- メタデータクライアントは allowlist を `export.arxiv.org`, `api.crossref.org`, `api.semanticscholar.org` に限定する。URL 取り込みは allowlist なしで IP 検証のみ。

## メタデータ解決（services/metadata）
- `identifiers.parse(query)`: `arXiv:2405.12345v2`、`https://arxiv.org/abs|pdf/...`、旧形式の `hep-th/9901001`、`doi:`、`https://doi.org/...` を受け付ける。`10.48550/arXiv.X` は arXiv として扱う。どれにも当たらなければタイトル扱い。
- arXiv は Atom API（defusedxml）、DOI は CrossRef `/works/{doi}`、タイトルは CrossRef `query.bibliographic` と S2 `/paper/search/match`（externalIds で DOI/arXiv を補う）を使う。
- 応答サイズの上限は 2MB、タイムアウトは設定値。外部エラーは `502`（`{"detail":"upstream error"}`）にまとめる。
- BibTeX はネットワークを使わずメタデータから決定的に生成する（`@article` / arXiv は `@misc` + eprint）。citekey は `{firstauthor_family}{year}{first_title_word}`（ASCII 小文字）とし、ワークスペース内で重複したら `a, b, …` を付ける。

## 取り込みパイプライン（services/ingest.py, storage/files.py, pdf.py）
1. **受け取り**: `.incoming/{uuid}.part`（library_root 配下なので rename/link がアトミック）にチャンクで書き込みながら、sha256 とサイズを計算する。上限は `max_upload_bytes`（middleware と二重に強制）。
2. **検証**: 拡張子 `.pdf`、Content-Type `application/pdf`（upload のみ）、先頭 1024 byte 内に `%PDF-` があること、pypdf で開けること（スレッドで実行）。失敗したら 415/422 を返し、一時ファイルは削除する。
3. **メタデータ**: 明示された identifier → PDF 先頭 2 ページから抽出した DOI/arXiv → resolver の順に試す。解決できなければ PDF の Title（なければファイル名）+ `generate_internal_identifier()` を使う。外部 API の失敗では取り込みを止めない。
4. **重複/紐付け**: `paper_id` 指定時、または同じワークスペースに同じ identifier の論文があり `has_pdf=false` のときは、その論文に紐付ける（wishlist は `unread` に変える）。`has_pdf=true` なら 409 を返す。
5. **共有配置**: `build_relative_path` → `resolve_in_library` → `os.link(tmp, target)`。`FileExistsError` のとき、sha256 が同じなら既存ファイルを再利用し、違えば `with_collision_index` で `_2`, `_3` … を試す（各候補も resolve_in_library を通す）。最後に tmp を unlink する。親ディレクトリは `mkdir(parents=True)`。
6. DB に保存（relative_file_path, file_sha256, file_size, page_count, has_pdf=true, index_status=pending）してから、Arq の `process_paper(paper_id)` を enqueue する。DB 保存に失敗し、ファイルが今回新しく作ったものなら unlink する。
- タイトルや年を PATCH で変えてもファイルはリネームしない（Obsidian 等からのパスを安定させるため）。

## API（api/v1/papers.py、prefix `/papers`）
権限: read 系は `read:papers` + viewer、write 系は `write:papers` + editor。ワークスペースの指定は `workspace_id`（省略時は personal）とし、`get_workspace_for_user` / `get_paper_for_user` / `accessible_workspace_ids` を必ず通す。

| Method / Path | 内容 |
|---|---|
| `GET /papers/light` | `q`（title/identifier/citekey/authors への ILIKE。ワイルドカードはエスケープ）, `tag`（名前）, `collection_id`, `year`, `status`, `workspace_id`, `limit`(≤200, 既定50), `offset`。§3.2 の 11 フィールドだけを返す配列。件数は `X-Total-Count` ヘッダ。tags は 1 クエリで集約し N+1 を避ける |
| `GET /papers/lookup?query=` | `LookupResult`（DB には保存しない）。レートリミット 30/分/ユーザー |
| `POST /papers/metadata-only` | `{identifier}` か `{title, authors?, year?}`、`workspace_id?`, `tags?`。解決して `status=wishlist, has_pdf=false` で保存。重複は 409。30/分 |
| `POST /papers/upload` | multipart: `file`, `workspace_id?`, `paper_id?`, `identifier?`, `status?`。201 + PaperDetail。60/分 |
| `POST /papers/import-url` | `{url, workspace_id?, paper_id?}`。`url` には PDF の URL、ランディングページ（`citation_pdf_url` meta を 1 回だけたどる）、`arxiv.org/abs/X`、DOI（`doi.org` 経由）、arXiv ID、PII を受け付ける。入力から見つかった識別子でメタデータを解決し、同じ識別子の wishlist があればそこに紐付ける（`services/remote_pdf.py`）。`URL_IMPORT_ENABLED` が false なら 404。safe_client で取得し、上限超過は中断する。10/分 |
| `GET /papers/{id}` | PaperDetail（abstract, bibtex, notes_md, tags, collection_ids, index_status 等） |
| `PATCH /papers/{id}` | title/authors/year/venue/abstract/status/citekey/notes_md/tags（名前のリスト。ワークスペース内で自動作成）/collection_ids（同じワークスペースのものか検証）。bibtex は再生成。citekey の重複は 409 |
| `GET /papers/{id}/file` | `resolve_in_library` を通した FileResponse（`application/pdf`, `inline`, `Cache-Control: private`）。PDF がなければ 404 |

## Worker（workers/tasks.py）
- `process_paper(paper_id)`: ファイルの存在と sha256 を確認し、page_count を確定させる。不一致なら `index_status=failed`。Phase 3 でこのタスクにチャンク分割と embedding を追加する。Phase 2 ではここまでが実処理で、index_status は `pending` のまま。
- `scan_watch_folder`（Arq cron、`WATCH_POLL_SECONDS` 間隔）:
  - `/data/watch/*/` の直下にある `*.pdf` だけを対象にする。dotfile と symlink は**追わずに除外**する。`_rejected` は対象外。
  - 安定判定: Redis に (size, mtime) を保存し、前回のスキャンから変化がなく、mtime が 5 秒以上前のものだけを処理する。処理はファイルごとに Redis `SET NX` のロックを取ってから行う。
  - slug → workspace を引き、`created_by` は workspace owner として ingest を呼ぶ。成功したら元ファイルを削除する。
  - 失敗（未知の slug、直下に置かれたファイル、検証 NG、重複）は `_rejected/{slug}/{name}` に移し、理由を `{name}.error.txt` に書く。

## 実装の進め方（subagent 委譲、Sonnet）
0. 親: ブランチ作成、`specs/phase2.md`、`schemas/metadata.py`（契約）、pyproject の依存追加 + `uv lock` を先に用意する。
1. **Agent A**（並列）: `core/net.py`、`services/metadata/*`、tests（`test_net.py`, `test_identifiers.py`, `test_metadata_clients.py`。arXiv/CrossRef/S2 の応答サンプルを `tests/fixtures/` に置き MockTransport で検証、`test_bibtex.py`）。
2. **Agent B**（並列）: `pdf.py`、`storage/files.py`、`paths.with_collision_index`、`ingest.py`、`api/v1/papers.py`、`workers/*`、`0002` migration、tests（`test_papers_api.py`, `test_upload.py`, `test_watch.py`, `test_import_url.py`）。resolver は fake に差し替える。テスト用 PDF はテキストを含む最小 PDF をバイト列で生成するヘルパーで作る。
3. 親: 結合（実 resolver を DI に接続）、全体テスト、レビュー（`/code-review`）、AGENTS.md §6 の更新、PR 作成。

## 検証
1. `INSTALL_DEV=true docker compose build biblioflow-api && docker compose up -d` → 5 サービスが healthy。
2. `docker compose exec -T biblioflow-api pytest -q -p no:cacheprovider` が全件 pass（skip 0）。主な観点:
   - **SSRF**: `127.0.0.1`, `10.x`, `::ffff:127.0.0.1`, `169.254.169.254`, `100.64.x`, `fd00::`, private IP へのリダイレクト、`:22` ポートをすべて拒否する。
   - **アップロード**: 拡張子偽装（中身が `%PDF-` でない）→415、上限超過→413、壊れた PDF→422。
   - **IDOR**: 他人の workspace の paper に対する GET/PATCH/file はすべて 404。viewer の upload/PATCH は 404/403。PAT の scope が read のみなら write は 403。
   - **ストレージ**: 同じ sha の二重配置は 1 ファイルを共有する。sha が違えば `_2`。テンプレートを変えても library 外に出ない。
   - **Watch**: 正常に取り込める、未知の slug は `_rejected` へ、symlink は無視、書き込み途中（サイズ変化中）のファイルはスキップ。
   - **light**: 各フィルタ、`X-Total-Count`、レスポンスのキーが 11 個ちょうどであること。
3. E2E（実ネットワーク、手動）: PAT で `lookup?query=2405.12345` → `metadata-only` → `upload` → `GET /papers/light` → `GET /papers/{id}/file`。`data/library/2024/2405.12345_*.pdf` がホスト側にできていること。`data/watch/{slug}/` に PDF を置き、10〜20 秒で取り込まれること。
4. CI が green（backend の skip 0、frontend の build、compose smoke）。
