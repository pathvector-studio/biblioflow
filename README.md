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
