# BiblioFlow

自己ホスト型の論文管理・AI リサーチアシスタント（Docker Compose 完結）。仕様は `AGENTS.md`、Phase 1 の計画は `specs/phase1.md` を参照。

## セットアップ

```bash
cp .env.example .env
# .env の change-me-* を置き換える（シークレット生成）
openssl rand -hex 32   # POSTGRES_PASSWORD / REDIS_PASSWORD / JWT_SECRET それぞれに使う
```

ローカルの http:// で試す場合は `.env` で `COOKIE_SECURE=false` にする。

```bash
docker compose up -d --build
docker compose ps        # 全サービスが healthy になることを確認
```

フロントエンドは http://localhost:3000 （`BIND_ADDRESS` / `FRONTEND_PORT` で変更可）。DB・Redis・API はホストに公開されない。

## Cloudflare Tunnel で外部からアクセスする（任意）

ポート開放なしで研究室や外出先から使える。トンネルはホストから外向きにだけ接続する。

### CLI で設定する（推奨）

API トークンを1つ用意する（ダッシュボードの My Profile > API Tokens で一度だけ作成）。必要な権限:
`Account > Cloudflare Tunnel > Edit`、`Zone > DNS > Edit`、`Zone > Zone > Read`。

```bash
export CLOUDFLARE_API_TOKEN=<API トークン>      # .env には書かない（アプリのコンテナに渡るため）
scripts/cloudflare-tunnel.sh papers.example.com   # トンネル・転送先・DNS・.env をまとめて設定
docker compose --profile cloudflare up -d
docker compose ps biblioflow-cloudflared         # healthy になれば接続済み
```

スクリプトは何度実行しても同じ結果になる。トンネル（既定名 `biblioflow`）を作成または再利用し、
`https://papers.example.com` → `http://biblioflow-frontend:3000` の転送と CNAME を設定し、
`.env` に `CLOUDFLARE_TUNNEL_TOKEN`、`COOKIE_SECURE=true`、`CORS_ALLOWED_ORIGINS` への https オリジン追加を書き込む。

### ダッシュボードで設定する場合

Zero Trust > Networks > Tunnels でトンネルを作り、Public hostname の Service に `http://biblioflow-frontend:3000` を指定する。
`.env` には上と同じ3項目（`CLOUDFLARE_TUNNEL_TOKEN`、`COOKIE_SECURE=true`、`CORS_ALLOWED_ORIGINS` に `https://<ホスト名>`）を書く。

`COOKIE_SECURE=true` の間は http://localhost からはログインできない（https のホスト名を使う）。
`ALLOW_REGISTRATION=false`（既定）のままにし、必要なら Cloudflare Access でホスト名の前に認証をかける。

## 管理者ユーザーの作成

```bash
docker compose exec biblioflow-api python -m app.cli create-user --email you@example.com --admin
```

## テスト

統合テストは `biblioflow_test` DB に対して実行される（`INSTALL_DEV=true` でビルドしたイメージが必要）。

```bash
INSTALL_DEV=true docker compose build biblioflow-api
docker compose run --rm -e COOKIE_SECURE=false biblioflow-api pytest
```

## ディレクトリ

- `data/library/` : `{Year}/{Identifier}_{SlugTitle}.pdf` で保存される PDF（ホストから直接参照可）
- `data/watch/` : 監視フォルダ（Phase 2 で有効化）
- `db/init/` : DB 初期化 SQL（初回起動時のみ実行）
