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
| `:8081` | 公開しない（`biblioflow-edge` 内だけ） | cloudflared 専用。クライアント IP は `CF-Connecting-IP` だけを信用する |
| `:8082` | 公開しない（`biblioflow-edge` 内だけ） | tailscale 専用。クライアント IP は `X-Forwarded-For`（tailscale serve が上書きする）だけを信用する。`CF-Connecting-IP` は信用しない（tailscale serve が素通しするため） |

リクエストボディの上限は、`/api/v1/papers/upload` が `MAX_UPLOAD_MB`、それ以外の API が 4 MiB。超えた要求はゲートウェイが API に渡す前に JSON の 413 で返す（API 側も同じ上限を持つ）。

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

#### Cloudflare Access をかける（推奨）

トンネルの手前（Cloudflare の edge）で認証をかける。BiblioFlow のログインより前に、許可したメールアドレスの本人確認（ワンタイム PIN など）が必要になる。

API トークンに次の権限を足す: `Account > Access: Apps and Policies > Edit`、`Account > Access: Service Tokens > Edit`。

```bash
scripts/cloudflare-access.sh papers.example.com me@example.com,you@example.com > access-token.txt
docker compose --profile cloudflare up -d     # Access を先にかけてから起動すると、無防備な時間がない
```

- ホスト全体（`/api` を含む）を保護する。ポリシーは 2 つ: 指定したメールアドレス（allow）と、サービストークン（Service Auth）。
- サービストークン（`biblioflow-<ホスト名>`、期限 1 年）の `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` は、作成時に一度だけ標準出力に出る。`.env` には書かない。失くしたら `--rotate` で再発行する（古いシークレットは無効になる）。
- 何度実行しても同じ結果になる（メールアドレスを変えたいときも再実行するだけ）。
- **PAT を使うクライアント（CLI・スクリプト・MCP）は、サービストークンのヘッダーも付ける。** 付けないと Access のログイン画面（302）に飛ばされる。

  ```bash
  curl -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
       -H "Authorization: Bearer bf_pat_..." https://papers.example.com/api/v1/papers/light
  ```
- Cloudflare の edge は、クライアントが付けた `CF-Connecting-IP` を 403 で拒否し、`X-Forwarded-For` は無視される（ゲートウェイのログと API には実際の接続元 IP が出る）。
- ゾーンで Web Analytics（RUM）の自動挿入が有効だと、Cloudflare が HTML に `static.cloudflareinsights.com` のスクリプトを差し込む。不要なら Cloudflare 側で無効にする。

## ログ

ゲートウェイ（Caddy）がすべてのリクエストを JSON で記録する。Cookie と Authorization ヘッダーは `REDACTED` に置き換わる。`request.client_ip` は解決済みのクライアント IP（Cloudflare / Tailscale 経由でも実際の接続元）。全コンテナのログは 10 MB × 5 世代でローテーションする。

```bash
# ログイン失敗（401）とレートリミット（429）
docker compose logs --no-color biblioflow-gateway | sed 's/^[^{]*//' \
  | jq -c 'select(.request) | select(.request.uri | startswith("/api/v1/auth/login")) | select(.status == 401 or .status == 429)
           | {ts, status, ip: .request.client_ip}'

# IP ごとの 4xx 件数
docker compose logs --no-color biblioflow-gateway | sed 's/^[^{]*//' \
  | jq -r 'select(.request) | select(.status >= 400 and .status < 500) | .request.client_ip' | sort | uniq -c | sort -rn | head
```

## バックアップと復元

`scripts/backup.sh` は、DB（`pg_dump`）と `data/library` のスナップショットを `BACKUP_DIR`（既定 `./backups`）に作る。ライブラリは前回のスナップショットとの差分だけをコピーし、変わっていない PDF はハードリンクで共有するので、毎日取っても容量はほとんど増えない。`BACKUP_KEEP`（既定 14）世代より古いものは消える。

```bash
BACKUP_DIR=/Volumes/nas/biblioflow scripts/backup.sh          # スナップショットを作る
scripts/restore.sh --check /Volumes/nas/biblioflow/<日時>      # 一時 DB に復元して検証する（本番データには触れない）
scripts/restore.sh --yes   /Volumes/nas/biblioflow/<日時>      # 本番を置き換える（直前の状態を自動でバックアップしてから）
```

- `BACKUP_DIR` は別のディスクか別のマシンに置く。同じディスク上のバックアップは、ディスクの故障に耐えない。
- `.env`（秘密情報）はバックアップに含めない。別の安全な場所に保管する。
- 定期実行は cron などで行う。例: `0 3 * * * cd ~/biblioflow && BACKUP_DIR=/mnt/nas/biblioflow scripts/backup.sh >> backups.log 2>&1`。週に 1 回は `--check` も回す。

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
