# BiblioFlow Phase 1: インフラ・DBスキーマ・セキュリティ基盤

## Context
リポジトリには AGENTS.md（仕様書）しかない状態。AGENTS.md の Phase 1 として、後続フェーズ（取り込み・RAG・UI）の土台になる部分を作る。対象は Docker 構成、DB スキーマ、認証/認可、パストラバーサル防止サニタイザ。
決定済み事項: **FastAPI 自前 JWT（HttpOnly Cookie）**、**Arq**、**Embedding 1024 次元**、**uv + Alembic**。
実装はトークン節約のため **Sonnet の subagent** に委譲する。親（Opus）はこの計画の作成と最終検証を担当する。

## ディレクトリ構成
```
biblioflow/
├── docker-compose.yml  .env.example  .gitignore  .dockerignore
├── specs/phase1.md                 # この計画のコピー（specスキル準拠）
├── db/init/01-extensions.sql       # vector, pg_trgm, citext + biblioflow_test DB作成
├── data/{library,watch}/.gitkeep   # ホストbind mount（中身はgitignore）
├── backend/
│   ├── Dockerfile  pyproject.toml  uv.lock  alembic.ini
│   ├── alembic/{env.py, versions/0001_initial.py}   # async env、手書きrevision
│   ├── app/
│   │   ├── main.py                 # app factory、middleware、router登録
│   │   ├── cli.py                  # `python -m app.cli create-user --admin`
│   │   ├── core/{config.py, security.py, ratelimit.py, middleware.py, errors.py}
│   │   ├── db/{base.py, session.py}
│   │   ├── models/{user,api_key,workspace,paper,chunk,annotation,tag,collection}.py
│   │   ├── schemas/{auth.py, token.py, user.py}
│   │   ├── api/deps.py             # Principal, require_scopes, workspace/paper アクセス検証
│   │   ├── api/v1/{router.py, health.py, auth.py, tokens.py, workspaces.py}
│   │   ├── services/storage/paths.py  # サニタイザ & パス解決
│   │   └── workers/settings.py     # Arq WorkerSettings（redis接続・起動時DB疎通確認）
│   └── tests/{conftest.py, test_paths.py, test_security.py, test_auth_api.py, test_tokens_api.py, test_rbac.py}
└── frontend/                        # Phase1は「ビルド可能な最小scaffold」のみ
    ├── Dockerfile  package.json  next.config.ts（output: standalone, /api rewrite）
    ├── tsconfig.json  tailwind/postcss設定  app/{layout.tsx,page.tsx,healthz/route.ts}
```

## インフラ（docker-compose.yml）
| service | image | networks | ports |
|---|---|---|---|
| biblioflow-db | pgvector/pgvector:pg16 | internal | なし |
| biblioflow-redis | redis:7-alpine（`--requirepass`、AOF） | internal | なし |
| biblioflow-api | ./backend | internal, edge | なし（expose 8000） |
| biblioflow-worker | ./backend（`arq app.workers.settings.WorkerSettings`） | internal, edge | なし |
| biblioflow-frontend | ./frontend | edge | `${BIND_ADDRESS:-127.0.0.1}:${FRONTEND_PORT:-3000}:3000` |

- `biblioflow-internal`: `internal: true`（外部への通信不可）。`biblioflow-edge`: API/Worker の外向き通信（CrossRef・LLM）と、frontend→api の通信に使う。
- ホストに公開するのは frontend だけ。ブラウザ→`/api/*` は Next の rewrite で `API_INTERNAL_URL=http://biblioflow-api:8000` へ転送する（Cookie は同一オリジンになる）。
- healthcheck: db=`pg_isready`、redis=`redis-cli -a ... ping`、api=`python -c urllib` で `/api/v1/healthz`、frontend=`node -e fetch` で `/healthz`。依存は `depends_on: condition: service_healthy` で表す。
- api の起動コマンド: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --proxy-headers --forwarded-allow-ips=*`
- volume: `pgdata`, `redisdata`（named）。`./data/library:/data/library`、`./data/watch:/data/watch`（bind）。
- 全サービスに `security_opt: [no-new-privileges:true]` を付ける。api/worker/frontend は `read_only: true` + `tmpfs: /tmp`。

## Dockerfile（非root）
- backend: `python:3.12-slim` を multi-stage で使う。builder で `uv sync --frozen --no-dev`、runtime で `ARG UID=1000 GID=1000` から `app` ユーザーを作成して `USER app`。`/data/*` の所有権は app にする。
- frontend: `node:22-alpine` を multi-stage（deps→build→runner）で使う。standalone 出力。`ARG UID/GID` で `nextjs` ユーザーを作る。

## .env.example（主要キー）
`POSTGRES_USER/PASSWORD/DB`, `DATABASE_URL`（asyncpg）, `REDIS_PASSWORD`, `REDIS_URL`, `JWT_SECRET`（32B以上必須、起動時に検証）, `JWT_EXPIRE_MINUTES=720`, `COOKIE_SECURE=true`, `CORS_ALLOWED_ORIGINS`, `ALLOW_REGISTRATION=false`, `MAX_UPLOAD_MB=50`, `LIBRARY_ROOT=/data/library`, `WATCH_DIR=/data/watch`, `FILENAME_TEMPLATE={year}/{identifier}_{slug_title}.pdf`, `EMBEDDING_DIM=1024`, `LLM_*`（Phase3用。キー名のみ定義）, `APP_UID/APP_GID=1000`, `BIND_ADDRESS`, `FRONTEND_PORT`, `API_INTERNAL_URL`
- config は pydantic-settings で読み込み、`SecretStr` で保持する。フロントへ渡す環境変数は `NEXT_PUBLIC_` を付けたものに限り、シークレットは一切渡さない。

## DBスキーマ（Alembic 0001、SQLAlchemy 2.0 Mapped、UUID PK、timestamptz）
- **users**: email(citext unique), password_hash, display_name, is_active, is_admin
- **api_keys**: user_id FK cascade, name, token_prefix（表示用の先頭12文字）, token_hash（sha256 hex, unique）, scopes text[], expires_at, last_used_at, revoked_at
- **workspaces**: name, slug unique, kind enum(`personal`,`shared`), owner_id。**ユーザー作成時に personal workspace を自動作成**し、全データを workspace に所属させる。これで「個人領域」と「研究室共有領域」を同じ RBAC で扱える。
- **workspace_members**: PK(workspace_id, user_id), role enum(`owner`,`editor`,`viewer`)
- **papers**: workspace_id FK, created_by, identifier, identifier_type enum(`arxiv`,`doi`,`internal`), doi, arxiv_id, citekey, title, authors JSONB, year, venue, abstract, bibtex, status enum(`unread`,`reading`,`read`,`wishlist`), has_pdf, relative_file_path, file_sha256, file_size, page_count, index_status enum(`pending`,`processing`,`ready`,`failed`), notes_md, `search_tsv` (GENERATED tsvector)
  - UNIQUE(workspace_id, identifier)。index: GIN trgm(title), GIN(search_tsv), (workspace_id,status), (workspace_id,year)
- **paper_chunks**: paper_id FK cascade, chunk_index, page_number, section, text, bboxes JSONB, token_count, embedding `vector(1024)` NULL可, `tsv` GENERATED。HNSW(vector_cosine_ops) と GIN(tsv)
- **annotations**: paper_id, user_id, kind enum(`highlight`,`area`,`note`), page_number, position JSONB（react-pdf-highlighter 互換）, content_text, comment, color
- **tags**（workspace_id, name, color, UNIQUE(ws,name)）と **paper_tags**。
- **collections**（workspace_id, name, parent_id）と **paper_collections**。light API の `collection_id` で使うため、マイグレーションのやり直しを避けて今入れておく。

## 認証・認可（app/core/security.py, app/api/deps.py）
- **パスワード**: argon2-cffi の Argon2id。ログイン失敗時は存在しない email でもダミーハッシュを検証し、タイミング差をなくす。
- **JWT**: PyJWT HS256。claims は `sub, jti, iat, exp, typ="session"`。`POST /auth/login` で Cookie `bf_session` を発行する（HttpOnly, Secure=COOKIE_SECURE, SameSite=Lax, Path=/）。`POST /auth/logout` は jti を Redis の denylist に TTL=残り有効期限で登録して失効させる。`GET /auth/me`。`POST /auth/register` は `ALLOW_REGISTRATION` が真のときだけ有効。
- **CSRF**: Cookie 認証で unsafe method（POST/PUT/PATCH/DELETE）が来たら、`Origin`/`Referer` が `CORS_ALLOWED_ORIGINS` に含まれることを要求する。PAT（Bearer）は対象外。
- **PAT**: `bf_pat_` + `secrets.token_urlsafe(32)`。平文はレスポンスで1回だけ返し、DB には SHA-256 だけを保存する。検証は hash で照合し、revoked/expired/inactive user なら拒否する。`last_used_at` は60秒以上空いたときだけ更新する。スコープは `read:papers`, `write:papers`。`GET/POST/DELETE /api/v1/tokens` は **session 認証のみ**（PAT で PAT を作れないようにする）。
- **Principal**: `get_principal` は Bearer `bf_pat_` を優先し、なければ Cookie を見る。session の scopes は全権扱い。`require_scopes(*s)` dependency を用意する。
- **IDOR 防止**: `get_workspace_for_user(ws_id, principal, min_role)` と `get_paper_for_user(paper_id, principal, min_role)` は membership を JOIN した1クエリで取得し、権限がなければ **404** を返す（存在の有無を漏らさない）。一覧系では `accessible_workspace_ids(user_id)` subquery を必ず使う。Phase1 では `GET /workspaces`, `POST /workspaces`（shared）, `POST /workspaces/{id}/members`（owner のみ）で RBAC を実際に動かして検証する。
- **レートリミット**: Redis の固定窓（INCR+EXPIRE）。`login` は IP+email で 10回/分と、IP 単位で 30回/分。汎用の `RateLimiter(key, limit, window)` を Phase3 の LLM エンドポイントでも使い回す。
- **middleware**: CORS（許可 origin のみ、credentials=true）、body size 上限（Content-Length 検査とストリーム累計で `MAX_UPLOAD_MB` を超えたら 413）、セキュリティヘッダ（`X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, HSTS は COOKIE_SECURE 時のみ）。本番では `/docs` を無効化できるようにする。

## ファイル名サニタイザ（app/services/storage/paths.py）
- `sanitize_identifier(raw)`: NFKC 正規化 → NUL・制御文字を除去 → `/ \ :` と空白を `_` に置換 → `[A-Za-z0-9._-]` 以外を除去 → `..` を `.` に縮約 → 先頭の `.`/`_`/`-` を除去 → 100文字で切る。空、`.`、`..` になったら `ValueError`。例: `10.1145/3651890` → `10.1145_3651890`
- `slugify_title(title, max_len=50)`: NFKD → ASCII 化 → 小文字化 → `[^a-z0-9]+` を `-` に置換 → 前後の `-` を除去 → 単語境界で切り詰め。空なら `untitled`。
- `generate_internal_identifier()`: `BF-` + uuid4 の base32 先頭10文字（外部依存なし）。
- `validate_template(tpl)`: `string.Formatter().parse` で `{year,identifier,slug_title}` 以外のプレースホルダ、書式指定、属性/添字アクセスを拒否する。絶対パス、`..`、`.pdf` 以外の拡張子も拒否する。起動時に設定値を検証する。
- `build_relative_path(year, identifier, title, template)`: 各値をサニタイズ（year は int か `Unknown`）してから format し、`PurePosixPath` の各パーツを再検査する。
- `resolve_in_library(relative, root)`: `(root/relative).resolve()` が `root.resolve()` 配下であることを `is_relative_to` で確認し、外れていれば `PathTraversalError`。シンボリックリンク経由の脱出もここで弾く。

## Worker（Phase1の範囲）
`app/workers/settings.py`: Arq の `WorkerSettings`（redis_settings は REDIS_URL から作る。on_startup/on_shutdown で DB エンジンを管理する）。実タスクは Phase2 以降で追加し、Phase1 ではヘルス用の `ping` タスクだけ実装する。TODO だけのスタブは置かない。

## 実装の進め方（subagent 委譲）
1. 親が `specs/phase1.md` にこの計画をコピーする。
2. **Agent A（general-purpose, model: sonnet）**: `backend/` 全体（pyproject/uv.lock、models、Alembic、auth、paths、tests、Dockerfile）。
3. **Agent B（general-purpose, model: sonnet）**: `docker-compose.yml`、`.env.example`、`.gitignore`/`.dockerignore`、`db/init`、`data/`、`frontend/` の scaffold と Dockerfile。
   - A と B は並列で動かす。両者の契約（サービス名、ポート8000、`/api/v1/healthz`、env キー名、UID/GID ARG）は上の表をそのままプロンプトに渡す。
4. 親が検証し、失敗箇所は該当 agent に SendMessage で修正を依頼する。

## 検証
1. `cd backend && uv run pytest tests/test_paths.py tests/test_security.py`: トラバーサル入力（`../../etc/passwd`, `a/../..`, `\x00`, `..\\`, Unicode 全角 `．．／`, シンボリックリンク脱出）、テンプレート攻撃（`{identifier.__class__}`, `/abs/{year}.pdf`）、PAT ハッシュ、JWT 期限切れ・改ざん。
2. `cp .env.example .env`（シークレット生成）→ `docker compose up -d --build` → `docker compose ps` で全サービスが healthy。
3. `docker compose exec biblioflow-api uv run pytest`（`biblioflow_test` DB に対する統合テスト）: login、cookie 属性、logout 後の失効、レートリミット 429、PAT の作成・使用・失効、スコープ不足で 403、他人の workspace で 404（IDOR）、Origin 不一致で 403（CSRF）。
4. ネットワーク分離: `docker compose port biblioflow-db 5432` が空、ホストから `nc -z localhost 5432/6379` が失敗する。
5. 非root: `docker compose exec biblioflow-api id` が `uid=1000`。frontend も同様。
6. E2E: `curl` で `http://localhost:3000/api/v1/healthz` が 200。`python -m app.cli create-user` → login → PAT 発行 → Bearer で `/auth/me` を叩く。
