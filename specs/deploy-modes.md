# 公開形態（LAN / ゼロトラスト / 一般公開）の整理

> 状態: **承認済み**（2026-11-03）。決定: 入口に Caddy ゲートウェイを置く、Tailscale は sidecar コンテナ（いずれもユーザー判断）。

## Context
BiblioFlow はユーザーによって公開の仕方が異なる。
- **LAN 内だけ**: http
- **ゼロトラスト**: Tailscale / Cloudflare Access
- **一般公開**: Cloudflare Tunnel

いまは形態ごとに `.env` の複数の項目を手で揃える必要があり、間違えるとログインできなかったり、CSRF で 403 になったりする。`COOKIE_SECURE`、`CORS_ALLOWED_ORIGINS`、`BIND_ADDRESS` の組み合わせがそれにあたる。

さらに、一般公開すると穴になる問題がある（コードで確認済み）。
- API は `uvicorn --proxy-headers --forwarded-allow-ips=*`（`backend/Dockerfile:34`）で動いている。この設定では `X-Forwarded-For` の**先頭**をクライアント IP として使う。
- Next は `x-forwarded-for` を `??=` で付けるだけ（`next/dist/server/base-server.js:567`）。そのため、クライアントが送った値が API までそのまま届く。
- ログインのレートリミットは IP 単位だけ（`api/v1/auth.py:54-55`）。ヘッダーを毎回変えれば無制限にパスワードを試せる。
- Next の rewrite プロキシには 30 秒のタイムアウトがある（`proxy-request.js`、`proxyTimeout || 30000`）。遅い回線での大きなアップロードや、Phase 3 の LLM ストリーミングがこれに当たる。

目標は次の 3 つ。
- アプリ本体は公開形態を意識しない作りのままにし、違いを「入口のコンテナ」と `.env` の数項目に閉じ込める
- どの形態でもクライアント IP と HTTPS かどうかを正しく判定する
- 3 形態それぞれの手順を README に用意する


## 方針
### 1. ゲートウェイを 1 つ置く（`biblioflow-gateway`、Caddy）
- ホストに公開するのはこのコンテナだけ（`${BIND_ADDRESS}:${FRONTEND_PORT}`）。frontend はポートを公開しなくなる。
  - これは AGENTS.md §4.4「公開するのは Frontend / API ゲートウェイのみ」に沿う。
- ルーティング:
  - `/api/*` → `biblioflow-api:8000` に直接送る。Next を経由しないので 30 秒のタイムアウトがなくなる。リクエストボディの上限は `MAX_UPLOAD_MB` に合わせる。
  - それ以外 → `biblioflow-frontend:3000`
  - Next の `/api` rewrite は `npm run dev` 用に残す。
- リスナーを 2 つに分ける。
  - **`:8080`（ホストに公開する）**: クライアントが送った `X-Forwarded-*` は捨てる。クライアント IP は接続元のアドレス、proto は `http` を Caddy が上書きでセットする（Caddy の既定の動作）。
  - **`:8081`（公開しない、edge ネットワーク内だけ）**: cloudflared / tailscale の sidecar 専用。これらが付ける `CF-Connecting-IP` / `X-Forwarded-For` / `X-Forwarded-Proto` を信用する。LAN からはこのポートに届かないので、偽装できない。
- 実行条件は既存のコンテナに合わせる: `caddy:2.x` をバージョン固定、非 root（UID 1000、非特権ポート）、`read_only`、`no-new-privileges`、healthcheck。
- 設定ファイル: `deploy/Caddyfile`（新規）。

### 2. API 側
- `--forwarded-allow-ips=*` は残す。API に届くのはゲートウェイを通ったリクエストだけで、ゲートウェイが `X-Forwarded-For` を上書きするので安全になる。その理由を Dockerfile にコメントで書く。
- `COOKIE_SECURE` 設定は廃止する。Cookie の Secure 属性と HSTS は、リクエストが https かどうか（`request.url.scheme`）で決める。
  - これで LAN（http）と Tunnel（https）を同時に使っても両方ログインできる。
  - 対象: `api/v1/auth.py` の `_set_session_cookie`、`core/middleware.py` の `SecurityHeadersMiddleware`、`main.py`、`core/config.py`。
- ログインのレートリミットに、メールアドレス単位の上限を足す（IP に依存しない多層防御）。既存の `core/ratelimit.py` の `enforce_rate_limit`（`window` 引数あり）を使い、例として 20 回 / 10 分。
- `CORS_ALLOWED_ORIGINS` は名前も役割もそのまま。ユーザーがブラウザで開く URL の一覧で、CSRF の許可リストを兼ねる。

### 3. 入口の profile
- `cloudflare`（既存）: README の転送先を `http://biblioflow-gateway:8081` に変える。
- `tailscale`（新規）:
  - `tailscale/tailscale` の sidecar を userspace networking で動かす（NET_ADMIN 不要）。
  - `TS_AUTHKEY` と state 用の volume を使い、`TS_SERVE_CONFIG` で `https://<name>.<tailnet>.ts.net` → `http://biblioflow-gateway:8081` に転送する。
  - Tailscale がクライアント IP を `X-Forwarded-For` で渡すかは実装時に確かめる。渡さない場合、IP 単位のレートリミットは tailnet 全体で共有になる（tailnet 内は信頼できる利用者なので許容）。
- LAN 内だけで使うなら profile は不要。

### 4. ドキュメント
- README に「公開形態」の節を作り、3 形態ごとに次をまとめる: `.env` で変える行、起動コマンド、Cloudflare / Tailscale 側の設定。
- 一般公開では Cloudflare Access を推奨すると明記する。
- `.env.example` から `COOKIE_SECURE` を消し、形態ごとに変える行をまとめる。
- Docker Desktop（Mac）の注意を書く: LAN からの接続元 IP がすべて VM のゲートウェイ IP に見えるため、IP 単位のレートリミットは共有になる。メール単位の上限で補う。
- AGENTS.md §6.2 に決定事項を追記する。

## 触るファイル
- `docker-compose.yml`: gateway を追加、frontend の `ports` を削除、tailscale profile を追加、CI 用の名前
- `deploy/Caddyfile`、`deploy/tailscale-serve.json`（どちらも新規）
- `backend/app/core/config.py`、`core/middleware.py`、`main.py`、`api/v1/auth.py`、`backend/Dockerfile`（コメント）
- `backend/tests/`: Cookie の Secure 判定、メール単位のレートリミット
- `.github/workflows/ci.yml`: compose スモークテストをゲートウェイ経由にする
- `.env.example`、`README.md`、`AGENTS.md`、`specs/deploy-modes.md`
- 自分のローカル `.env`: `COOKIE_SECURE` を削除する（変更前に確認する）

## スコープ外
- LAN を HTTPS にすること（Caddy の内部 CA など）
- アプリ側で Cloudflare Access の JWT（`Cf-Access-Jwt-Assertion`）を検証すること
- 対話式のセットアップスクリプト
- Tailscale をホストにインストールする構成（README で触れる程度）

## 検証
1. backend pytest: 全件 pass、skip 0。新規テストは次の 3 つ。
   - `X-Forwarded-Proto: https` のときだけ Cookie に Secure が付く
   - メール単位のレートリミット
   - CSRF の既存テストが通る
2. `docker compose up -d --build` で 6 サービスが healthy。`http://192.168.1.10:3080` でログイン・アップロード・PDF 表示ができる（Playwright で前回の t1 / t2 / t8 を再実行）。
3. なりすましの確認: ゲートウェイに `X-Forwarded-For` を毎回変えて誤パスワードでログインを 31 回送ると、429 になる（今は通ってしまう）。
4. `:8081` がホストからも LAN からも届かないこと（`curl` が接続拒否になる）。
5. 50MB 近い PDF をアップロードしても 30 秒で切れないこと（帯域を絞った curl で確認）。
6. `cloudflare` / `tailscale` profile は、トークンがないので `docker compose --profile ... config` が通ることまで確認する。実接続はユーザー環境で確かめてもらう。
7. CI green、PR を作る。

## 実装メモ
- イメージ: `caddy:2.10.2-alpine`、`tailscale/tailscale:v1.90.9`（どちらも pull で存在確認済み）。
- Caddy は `cap_drop: ALL` だとバイナリのファイル capability（net_bind_service）のせいで exec できない（`operation not permitted`）。`cap_add: NET_BIND_SERVICE` を足して解決。ポートは 8080/8081 なので実際には使わない。
- 健康チェックは admin API を使わず、`:8080` の `/gateway-healthz`（`respond 200`）に wget。`admin off`、`auto_https off`、`persist_config off`。
- ボディ上限は `{$MAX_UPLOAD_MB}MiB`（API と同値。マージンは Caddyfile で足し算できないので付けていない）。ボディ超過は API の JSON 413 が返る（Expect: 100-continue 付きの curl だとクライアント側で接続リセットになる場合がある。ブラウザは Expect を付けない）。
- `X-Forwarded-For` は両リスナーとも `header_up X-Forwarded-For {client_ip}` で単一の IP に上書きしている。:8080 では偽装 XFF / CF-Connecting-IP / X-Forwarded-Proto を無視することを実測で確認した。
- Tailscale: `tailscale serve` のプロキシは `SetXForwarded` を使い（tailscaled バイナリに `SetXForwarded` / `X-Forwarded-For|Host|Proto` / `Tailscale-User-Login` の文字列を確認）、tailnet 上の接続元 IP を `X-Forwarded-For`、`X-Forwarded-Proto: https` として付ける。Go の `SetXForwarded` は受信した XFF を捨てて上書きするので偽装はできない。実接続は authkey がないため未確認（バイナリ解析ベース）。Caddy :8081 は `client_ip_headers CF-Connecting-IP X-Forwarded-For` でこれを拾うので、IP 単位のレートリミットは tailnet の端末ごとに分かれる。
- Tailscale コンテナは root のまま（公式イメージの containerboot が state volume とソケットに書き込むため）。`cap_drop: ALL` と `no-new-privileges` で起動することは確認した（authkey なしで認証 URL を出すところまで）。healthcheck は省略（認証前に unhealthy になり depends_on の対象にもしないため）。
- `SecurityHeadersMiddleware` のコンストラクタ引数 `hsts` を廃止（リクエストごとに scope["scheme"] で判定）。

## 追補（2026-11-04、セキュリティレビュー）
- **Tailscale 経由のクライアント IP 偽装を塞いだ**。tailscale serve は X-Forwarded-For を上書きするが、クライアントが送った `CF-Connecting-IP` は素通しする。`:8081` は `client_ip_headers CF-Connecting-IP X-Forwarded-For`（先に見つかった方を使う）だったので、tailnet の端末は任意の IP を名乗れ、IP 単位のレートリミットを回避できた。
  - 対策として、リスナーを sidecar ごとに分けた。`:8081` は cloudflared 専用で `CF-Connecting-IP` だけを信用する。`:8082` は tailscale 専用で、`X-Forwarded-For` を `trusted_proxies_strict` で使う。
  - 全リスナーで `CF-Connecting-IP` を API に転送しない。
  - edge ネットワーク上のコンテナから各ポートに `CF-Connecting-IP: 6.6.6.6` と `X-Forwarded-For: 9.9.9.9, 7.7.7.7` を送り、API のアクセスログで確認した。:8080 は接続元、:8081 は 6.6.6.6、:8082 は 7.7.7.7 になった。
- **ボディ上限をルートごとに分けた**。以前は全エンドポイントが `MAX_UPLOAD_MB`（50 MiB）で、未認証の `/auth/login` でも、レートリミットの前に 50 MiB の JSON を読み込んでいた。
  - 今は upload だけ `MAX_UPLOAD_MB`、それ以外の API は 4 MiB にした（ノートは最大 50 万字）。
  - 同じ上限を Caddy（`body_limit` スニペット）と API の `BodySizeLimitMiddleware` の両方で持つ。frontend は Caddy で 1 MiB。
  - Content-Length が上限を超える要求は、Caddy が上流に流す前に JSON の 413 を返す。API に途中まで流してから API が 413 を返すと、API が接続をリセットした時点で Caddy の書き込みが失敗し、5〜8 割が 502 になった（CI でも発生）。chunked は `request_body max_size` で 413 になる。ゲートウェイ経由で 4 MiB+ / 5 MiB / chunked をそれぞれ 20 回送り、すべて 413 になることを確認した。

