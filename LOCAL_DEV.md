# ローカル開発

開発用のチェックアウトを動かすのに何を用意すればよいかをまとめる。本番の入れ方は [README の Setup](README.md#setup)（`install.sh`）を参照。

## 用意するもの

| もの | 用途 | 入手 |
|---|---|---|
| Docker（Compose v2） | 全サービスの起動とテスト | Docker Desktop / OrbStack など |
| git、`openssl` | clone、秘密の生成 | macOS に標準で入っている |
| [uv](https://docs.astral.sh/uv/) | `mcp/` のテスト、backend をコンテナの外で触るとき | `brew install uv` |
| Node.js 22 と npm | フロントの lint / typecheck / build | `brew install node@22` |
| `gh` | PR とマージ | `brew install gh`、`gh auth login` |
| 1Password CLI（任意） | 外部トークンを `op://` 参照で渡す | `brew install --cask 1password-cli`、アプリの「設定 > 開発者 > CLI と連携」 |

**リポジトリの外に置くもの**（`scripts/dev-setup.sh` が作る。git には入らない）:

```
~/.config/biblioflow/            (700)
  dev.env            (600)  .env の実体。DB・Valkey・JWT の秘密、ポート、公開範囲、許可するオリジン
  AGENTS.local.md           このマシンのメモ（コーディングエージェント向け。秘密は書かない）
  mcp.env            (600)  biblioflow-mcp の接続先、PAT、Access のサービストークン
  cloudflare.env     (600)  CLOUDFLARE_API_TOKEN（Cloudflare のスクリプトを動かすときだけ渡す）
```

チェックアウトの `.env` と `AGENTS.local.md` は、ここへの symlink になる。compose はこれまでどおり `.env` を読む。

## 初回

```bash
git clone git@github.com:pathvector-studio/biblioflow.git && cd biblioflow
scripts/dev-setup.sh                 # 秘密を生成し、symlink と git フックを設定する（何度実行してもよい）
# ポートや LAN 公開を変えるとき（初回だけ有効。あとは ~/.config/biblioflow/dev.env を直接直す）:
#   scripts/dev-setup.sh --port 3080 --bind 0.0.0.0 --origin http://192.168.1.10:3080
docker compose up -d --build
docker compose ps                    # db / redis / api / worker / frontend / gateway / embedder / model-init が healthy
docker compose exec biblioflow-api python -m app.cli create-user --email you@example.com --admin
open http://localhost:3000
```

チェックアウトに `.env` がすでにある場合、`dev-setup.sh` はそれを `~/.config/biblioflow/dev.env` に移して symlink にする（上書きはしない）。終わりに、まだ `change-me-*` のままの項目、`.env.example` にあって `dev.env` にない項目（既定値が使われる）、もう使われていない項目を、名前だけ表示する。

ブラウザで開く URL はすべて `CORS_ALLOWED_ORIGINS` に入れる。ないと、ログインなどの書き込みが CSRF の検査で 403 になる。

## テスト

```bash
INSTALL_DEV=true docker compose build biblioflow-api
docker compose run --rm --no-deps -v "$PWD/backend/app:/app/app:ro" -v "$PWD/backend/tests:/app/tests:ro" \
  -v "$PWD/backend/alembic:/app/alembic:ro" -e ENABLE_DOCS=false biblioflow-api pytest -q -p no:cacheprovider
(cd frontend && npm ci && npm run lint && npm run typecheck && npm run build)
uv run --directory mcp pytest
python3 scripts/check-private-info.py
```

テスト用の DB（`biblioflow_test`）の接続先は compose が `POSTGRES_*` から組み立てる。用意するものはない。

フロントだけを `npm run dev` で動かすときは、`/api` の転送先をゲートウェイにする: `cd frontend && API_INTERNAL_URL=http://localhost:3000 npm run dev -- -p 3001`。このとき `CORS_ALLOWED_ORIGINS` に `http://localhost:3001` を足す。

## 設定の分類

`.env.example` に全項目がダミー値で載っている。実値は `~/.config/biblioflow/` にだけ置く。

| 分類 | 項目 | 置き場 |
|---|---|---|
| A. 公開してよい設定 | `POSTGRES_USER` `POSTGRES_DB` `JWT_EXPIRE_MINUTES` `ALLOW_REGISTRATION` `ENABLE_DOCS` `MAX_UPLOAD_MB` `LIBRARY_ROOT` `WATCH_DIR` `FILENAME_TEMPLATE` `EXTERNAL_HTTP_TIMEOUT` `WATCH_POLL_SECONDS` `URL_IMPORT_ENABLED` `EMBEDDING_*`（キーを除く） `SEARCH_MIN_SIMILARITY` `LLM_PROVIDER` `LLM_MODEL` `VISION_MODEL` `APP_UID` `APP_GID` `API_INTERNAL_URL` `BF_INSTANCE` `COMPOSE_PROJECT_NAME` `COMPOSE_PROFILES` `TS_HOSTNAME`、`BACKUP_*`、installer の `BIBLIOFLOW_*` | 既定値は `.env.example` とコード。変えるなら `dev.env` |
| B. 秘密 | `POSTGRES_PASSWORD` `REDIS_PASSWORD` `JWT_SECRET`（マシンごとに生成する） | `dev.env` |
| | `CLOUDFLARE_TUNNEL_TOKEN` `TS_AUTHKEY` `LLM_API_KEY` `EMBEDDING_API_KEY` `SEMANTIC_SCHOLAR_API_KEY` | `dev.env`（compose がコンテナに渡すため） |
| | `CLOUDFLARE_API_TOKEN` | `cloudflare.env`（アプリのコンテナに渡さない） |
| | `BIBLIOFLOW_TOKEN`（PAT） `CF_ACCESS_CLIENT_ID` `CF_ACCESS_CLIENT_SECRET` | `mcp.env` |
| C. 個人・ローカル固有 | `CORS_ALLOWED_ORIGINS` `BIND_ADDRESS` `FRONTEND_PORT`（LAN の IP や開発用ホスト名を含む） `CROSSREF_MAILTO` `BIBLIOFLOW_URL` | `dev.env` / `mcp.env` |
| | ほかのサービスとのポートの衝突、テスト用ユーザーのメール、開発用ホスト名 | `AGENTS.local.md` |

`DATABASE_URL` / `TEST_DATABASE_URL` / `REDIS_URL` は compose が B から組み立てるので、どこにも書かない。テスト用ユーザーのパスワードは、どのファイルにも書かない。

## 外部トークン（1Password）

マシンごとに作り直せない秘密（Cloudflare の API トークン、Access のサービストークン、PAT、LLM のキー）は 1Password に保存して、ファイルには参照だけを書く:

```bash
# ~/.config/biblioflow/cloudflare.env
CLOUDFLARE_API_TOKEN=op://<vault>/biblioflow-cloudflare-api/credential
# ~/.config/biblioflow/mcp.env
BIBLIOFLOW_URL=https://papers.example.com
BIBLIOFLOW_TOKEN=op://<vault>/biblioflow-pat/credential
CF_ACCESS_CLIENT_ID=op://<vault>/biblioflow-access-service-token/username
CF_ACCESS_CLIENT_SECRET=op://<vault>/biblioflow-access-service-token/credential
```

`scripts/with-env.sh <ファイル> -- <コマンド>` で渡す。ファイルに `op://` があれば `op run` で解決し（出力に値が出ると伏せ字になる）、なければ平文の `KEY=value` として読む。権限が 600 でないファイルは拒否する。

```bash
scripts/with-env.sh ~/.config/biblioflow/cloudflare.env -- scripts/cloudflare-access.sh papers.example.com you@example.com
claude mcp add biblioflow -- "$PWD/scripts/with-env.sh" ~/.config/biblioflow/mcp.env -- uv run --directory "$PWD/mcp" biblioflow-mcp
```

`claude mcp add -e BIBLIOFLOW_TOKEN=...` は、トークンを Claude Code の設定ファイルに平文で残すので使わない。

`dev.env` の DB・Valkey・JWT の秘密は 1Password に入れない（マシンごとに `dev-setup.sh` が生成する。消えても作り直せばよい。ただしその場合は DB のボリュームも作り直すことになる）。`CLOUDFLARE_TUNNEL_TOKEN` などコンテナに渡すものは、1Password から `dev.env` に貼る。

## 開発用の Cloudflare Tunnel

- **トンネルを動かすマシンは 1 台だけ**（Mac mini）。同じトンネルのトークンを 2 台で動かすと、Cloudflare が両方に振り分けてしまう。ほかのマシン（MacBook Air など）の `dev.env` には `CLOUDFLARE_TUNNEL_TOKEN` を入れない。
- **Access を先にかける**。`scripts/cloudflare-access.sh <hostname> <email,...>` を実行してから `docker compose --profile cloudflare up -d` する。起動の前後に `scripts/check-access.sh <hostname>` を実行し、すべてのパス（`/api` を含む）が Access のログインか 401/403 で止まることを確かめる。`OPEN` が 1 つでも出たら cloudflared を止める。
- 開発用ホスト名は `AGENTS.local.md` にだけ書く（公開する文章では `papers.example.com`）。

## 別のマシンで始めるとき

1. clone して `scripts/dev-setup.sh`（秘密はそのマシンで新しく生成される。前のマシンの `dev.env` はコピーしない）。
2. 1Password CLI を入れてサインインする。`mcp.env` と `cloudflare.env` に `op://` 参照を書く（上の例）。
3. `AGENTS.local.md` にそのマシンのこと（ポート、ほかのサービス、トンネルを動かすかどうか）を書く。
4. メモリの小さいマシンでは `EMBEDDING_PROVIDER=none` のままにする（`local` は embedder だけで約 2 GB を使う）。

## 秘密情報の扱いのルール

人もコーディングエージェントも、次を守る（`AGENTS.md` の 6.2 にも同じものがある）。

- 秘密の実値は、コミット・PR・issue・ドキュメント・ログ・コーディングエージェントのメモリや会話の要約に書かない。参照するときは変数名だけを使う。
- `.env`（`dev.env`）、`mcp.env`、`cloudflare.env` の中身は表示しない。調べるときは、キー名と「設定済み・空・`change-me` のまま」だけを出す。
- 値を渡すときは compose、`scripts/with-env.sh`、`op run` を使う。コマンドラインの引数や `echo` に値を書かない。
- 個人・ローカル固有の情報は `AGENTS.local.md` に書く。公開する文章では例の値（`192.168.1.10`、`papers.example.com`、`you@example.com`、`<host>.<tailnet>.ts.net`）を使う。
- 新しい秘密を足すときは、`.env.example` にダミー値（`change-me-*` か空）で載せ、backend では `SecretStr` で受ける。
- コミットの前に `scripts/check-private-info.py` が動く（`dev-setup.sh` が git フックを設定する）。LAN の IP・ホーム以下のパス・個人のメール・トークンの形をした値を拒否する。
