# BiblioFlow

自己ホスト型の論文管理・AI リサーチアシスタント（Docker Compose 完結）。仕様は `AGENTS.md`、Phase 1 の計画は `specs/phase1.md` を参照。

## セットアップ

```bash
cp .env.example .env
# .env の change-me-* を置き換える（シークレット生成）
openssl rand -hex 32   # POSTGRES_PASSWORD / REDIS_PASSWORD / JWT_SECRET それぞれに使う
```

```bash
docker compose up -d --build
docker compose ps        # 全サービスが healthy になることを確認
```

ホストに公開されるのは `biblioflow-gateway`（Caddy）の 1 ポート（`BIND_ADDRESS:FRONTEND_PORT`、既定 http://127.0.0.1:3000）だけ。DB・Redis・API・frontend は公開されない。ゲートウェイは `/api/*` を API へ直接、それ以外を frontend へ転送する。

## 公開形態

アプリ本体は公開形態を意識しない。違いは `.env` の数項目と、入口のコンテナ（profile）だけ。Cookie の Secure 属性と HSTS はリクエストが https かどうかで自動的に決まるので、LAN（http）とトンネル（https）を同時に使ってもよい。

ゲートウェイは 2 つのリスナーを持つ。

| ポート | 公開 | 用途 |
|---|---|---|
| `:8080` | ホストに公開（`BIND_ADDRESS:FRONTEND_PORT`） | クライアントが直接つなぐ口。クライアントが送った `X-Forwarded-*` は捨て、接続元アドレスから上書きする |
| `:8081` | 公開しない（`biblioflow-edge` 内だけ） | cloudflared / tailscale 専用。`CF-Connecting-IP` / `X-Forwarded-*` を信用する |

### A. LAN 内だけ（http）

`.env`:

```bash
BIND_ADDRESS=0.0.0.0            # 自分の PC だけなら 127.0.0.1
FRONTEND_PORT=3000
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://192.168.1.10:3000   # ブラウザで開く URL をすべて列挙
```

起動: `docker compose up -d --build`。外部側の設定は不要。

Docker Desktop（Mac）では、LAN の他端末からの接続元 IP がすべて VM のゲートウェイ IP に見える。そのため IP 単位のレートリミットは全端末で共有になる。ログインにはメールアドレス単位の上限（20 回 / 10 分）もあり、これで補う。

### B. Tailscale（ゼロトラスト）

ポートは開けない。tailnet に参加している端末だけがつなげる。

1. Tailscale 管理画面の DNS で MagicDNS と HTTPS Certificates を有効にする
2. Settings > Keys で auth key を発行する（One-off 推奨）
3. `.env`:

   ```bash
   BIND_ADDRESS=127.0.0.1
   TS_AUTHKEY=tskey-auth-...
   TS_HOSTNAME=biblioflow
   CORS_ALLOWED_ORIGINS=https://biblioflow.<tailnet>.ts.net,http://localhost:3000
   ```

4. 起動: `docker compose --profile tailscale up -d --build`
5. `docker compose logs biblioflow-tailscale` で接続を確認し、https://biblioflow.<tailnet>.ts.net を開く。初回ログイン後は `TS_AUTHKEY` を空に戻してよい（状態は `tailscale-state` volume に残る）

tailnet の中だけに公開される（Funnel は使わない）。

### C. Cloudflare Tunnel（一般公開）

ポート開放なしで外から使える。トンネルはホストから外向きにだけ接続する。**公開する場合は Cloudflare Access（Zero Trust のアクセスポリシー）でホスト名の前に認証をかけることを推奨する。** `ALLOW_REGISTRATION=false`（既定）のままにする。

まず `.env` の `BIND_ADDRESS=127.0.0.1` を確認する（LAN に公開しない場合）。

#### CLI で設定する（推奨）

API トークンを1つ用意する（ダッシュボードの My Profile > API Tokens で一度だけ作成）。必要な権限:
`Account > Cloudflare Tunnel > Edit`、`Zone > DNS > Edit`、`Zone > Zone > Read`。

```bash
export CLOUDFLARE_API_TOKEN=<API トークン>      # .env には書かない（アプリのコンテナに渡るため）
scripts/cloudflare-tunnel.sh papers.example.com   # トンネル・転送先・DNS・.env をまとめて設定
docker compose --profile cloudflare up -d --build
docker compose ps biblioflow-cloudflared         # healthy になれば接続済み
```

スクリプトは何度実行しても同じ結果になる。トンネル（既定名 `biblioflow`）を作成または再利用し、
`https://papers.example.com` → `http://biblioflow-gateway:8081` の転送と CNAME を設定し、
`.env` に `CLOUDFLARE_TUNNEL_TOKEN` と、`CORS_ALLOWED_ORIGINS` への https オリジン追加を書き込む。

#### ダッシュボードで設定する場合

1. Zero Trust > Networks > Tunnels でトンネルを作成し、トークンを控える
2. Public hostname を追加し、Service に `http://biblioflow-gateway:8081` を指定する（`:8080` ではない）
3. `.env`:

   ```bash
   CLOUDFLARE_TUNNEL_TOKEN=<トークン>
   CORS_ALLOWED_ORIGINS=https://papers.example.com,http://localhost:3000
   ```

4. `docker compose --profile cloudflare up -d --build`

Access を使う場合は Zero Trust > Access > Applications で `papers.example.com` を保護するポリシーを作る。

## 管理者ユーザーの作成

```bash
docker compose exec biblioflow-api python -m app.cli create-user --email you@example.com --admin
```

## テスト

統合テストは `biblioflow_test` DB に対して実行される（`INSTALL_DEV=true` でビルドしたイメージが必要）。

```bash
INSTALL_DEV=true docker compose build biblioflow-api
docker compose run --rm biblioflow-api pytest
```

## ディレクトリ

- `data/library/` : `{Year}/{Identifier}_{SlugTitle}.pdf` で保存される PDF（ホストから直接参照可）
- `data/watch/` : 監視フォルダ（Phase 2 で有効化）
- `db/init/` : DB 初期化 SQL（初回起動時のみ実行）
