# v1 の基準 3: 互換性の約束（OpenAPI の公開・破壊的変更の検出・アップグレードの CI）

> 状態: 承認待ち（2026-11-13、ループが作成）

## 目的
v1.0 の基準 3（`RELEASING.md`）は「`/api/v1`・PAT のスコープ・MCP のツール・`.env` のキー・保存の配置を文書にし、semver で固定する」こと。確かめ方は「リリースに OpenAPI を添えて公開する」「CI が前のタグを入れてからアップグレードし、そのバックアップで `restore.sh --check` を通す」の 2 つ。今はどちらもなく、API の破壊的変更はレビューで気づくしかなく、アップグレードはタグ前の手作業のチェックリスト（`RELEASING.md` の「Upgrade」）だけで確かめている。

## 要件
- [ ] R1: **OpenAPI のスナップショット**: `/api/v1` の OpenAPI を DB もネットワークもなしに作るスクリプト（`scripts/openapi.py`、`app.openapi()` を使う。`ENABLE_DOCS` に関係なく作れる）と、その結果をリポジトリに置いた `docs/api/openapi.json`（キーを並べた決定的な JSON）。CI の backend ジョブが「コードから作った結果とファイルが一致すること」を検査し、ずれていたら作り直すコマンドを出して落ちる。
- [ ] R2: **破壊的変更の検出**: `scripts/openapi.py diff <old> <new>` が、前のリリースタグの `docs/api/openapi.json` と今のものを比べ、次を「破壊的」として列挙する: パス・メソッドの削除、レスポンスのプロパティの削除・型の変更・必須から任意への変更、リクエストに新しい必須のパラメータ・プロパティ、既存のパラメータの型の変更・任意から必須への変更、enum の値の削除（リクエスト側）、成功時のステータスコードの削除。追加（新しいパス・任意のパラメータ・レスポンスのプロパティ）は「互換」として表示だけする。標準ライブラリだけで書く。
- [ ] R3: **CI での判定**: PR と main への push で、前のリリースタグ（`vX.Y.Z` で rc を除く最新。今の `VERSION` と同じタグは除く）と比べる。破壊的変更があれば落ちる。ただし PR に `breaking-api` ラベルが付いていて、`CHANGELOG.md` の `[Unreleased]` に `### Breaking` の行があれば通す（0.x では MINOR、1.0 以降は MAJOR を上げる約束を `RELEASING.md` に書く）。前のタグに `docs/api/openapi.json` がない間（v0.2.0）は比較を飛ばし、その旨を表示して通す。
- [ ] R4: **リリースへの添付**: `RELEASING.md` の「3. Tag and publish」の `gh release create` に `docs/api/openapi.json` を足す（`install.sh` と同じ扱い。手で作るリリースなので新しいワークフローは足さない）。チェックリストに「破壊的変更の一覧（R2 の出力）が changelog の Breaking と合っている」を足す。
- [ ] R5: **アップグレードの CI**: ci.yml に `upgrade` ジョブを足す（既存のジョブと同じ action・同じ固定。新しい action は使わない）。手順: 前のリリースタグを `install.sh`（`BIBLIOFLOW_REF=<前のタグ>`、別の `BIBLIOFLOW_DIR`、意味検索は no）で入れて起動 → `cli create-user` でユーザーを作り、API で PDF つきの論文 2 件（CI の中で作った小さな PDF を `/papers/upload`）、書誌だけの論文 1 件（`/imports` に BibTeX。外部 API を呼ばないため）、タグ・ノートを入れる → `scripts/backup.sh` → 本番と同じく前のタグの `scripts/deploy.sh` で今のコミットに更新（コミットはジョブのチェックアウトから `git fetch` で渡す）→ 確かめる: アップグレード前に取った Cookie と PAT がそのまま使える、`alembic current` が head、論文の件数・タグ・ノートが同じ、`/papers/{id}/file` が同じ sha256 の PDF を返す、キーワード検索が当たる、`scripts/restore.sh --check <アップグレード前のバックアップ>` が通る。失敗したら全サービスのログを出す。
- [ ] R6: **いつ動かすか**: `upgrade` は PR・main への push・タグの push で動かす（`compose` と並列。2 回ビルドするので 10 分前後を見込む）。前のリリースタグがない場合は飛ばす。
- [ ] R7: ドキュメント: `RELEASING.md` の基準 3 の状態と確かめ方（R1〜R5 の場所）、チェックリストの「Upgrade」を「CI の `upgrade` ジョブが通っていること＋セッションの継続を手で 1 回」に縮める。AGENTS.md 6.2 に 1 行（API を変えたら `scripts/openapi.py` で作り直す、破壊的なら `breaking-api` ラベル）。STATUS.md。利用者向けの変化はないので CHANGELOG は `skip-changelog`（ただし「リリースに OpenAPI を添える」ことは次のリリースの Added に 1 行書く）。

## スコープ外
- MCP のツールの引数・`.env` のキー・保存の配置・PAT のスコープの自動検出（基準 3 の「文書にして固定」は `RELEASING.md` の public interface の表で足りるとみなす。自動化するなら次の計画で。どれも OpenAPI より変化が少ない）。
- oasdiff など外部の差分ツール（依存の追加・外部ソフトのダウンロードになる。R2 の規則で足りなければ次の計画で検討）。
- リリースを作るワークフロー（タグの push で Release を自動で作る等）。リリースは今までどおり保守者が手で作る。
- 前のタグより古い版（2 つ前以上）からのアップグレード、ダウングレード、`embedding` を `local` にした状態でのアップグレード（モデルの取得が要るため）。
- OpenAPI から作るドキュメントのサイト・クライアントの生成。

## 受け入れ基準
- `uv run python scripts/openapi.py > /tmp/o.json && diff /tmp/o.json docs/api/openapi.json` が差分なし。API のコードを変えてスナップショットを作り直さないと、CI の backend ジョブが落ちる。
- `scripts/openapi.py diff` のテスト（偽の 2 つの OpenAPI で、R2 の破壊的な場合それぞれと、互換な追加）が通る。
- 試しの PR（マージしない）で `/papers/light` のレスポンスから 1 フィールドを消すと CI が落ち、`breaking-api` ラベルと Breaking の行で通る。
- v0.3.0 のタグが打たれた後の PR で、`upgrade` ジョブが緑（タグ前は v0.2.0 から。v0.2.0 には `openapi.json` がないので R3 は飛ばされ、R5 は動く）。
- `upgrade` ジョブで、わざとマイグレーションを壊したコミット（試しの PR）では赤になる。

## 決めたこと
- **スナップショットをリポジトリに置く**: 前のタグの OpenAPI を、そのタグのコードを動かさずに `git show` で取れる。PR の差分にも API の変化が見える。
- **差分の検査は自前（標準ライブラリ）**: 依存もダウンロードも増やさない。規則は R2 に書いたものだけで、FastAPI が出す OpenAPI の範囲（`$ref` は `components/schemas` だけ）に絞る。
- **アップグレードは `install.sh` で入れて `deploy.sh` で上げる**: 利用者が入れる方法と本番が更新する方法をそのまま通す。`install.sh` はタグしか取らないので、今のコミットはジョブのチェックアウトから渡す。
- **新しいワークフローではなく ci.yml のジョブ**: 新しい action も使わない（`specs/agent-workflow.md` の承認が要る項目を避ける）。それでも CI に 10 分前後のジョブが増えるので、承認時に判断してほしい（パスで絞る案: `backend/`・`frontend/`・`docker-compose.yml`・`deploy/`・`scripts/`・`install.sh` に触れた PR だけ）。
- 書誌だけの論文は `/papers/metadata-only` ではなく `/imports` で入れる（metadata-only は外部の書誌 API を呼ぶので CI が不安定になる）。
