# セキュリティの基盤（v1 の基準 4）

> 状態: 承認済み（2026-11-12、private vulnerability reporting の有効化も承認）。このセッションで実装中

## 目的
v1 の基準 4（`RELEASING.md`）を満たすため。脆弱性の報告先と対応の約束を公開し、コード・コンテナイメージ・CI の設定の既知の問題を、PR と定期実行で自動的に見つける。今はどれもない（Dependabot・secret scanning・push protection・依存の監査 `audit.yml` だけ）。

## 要件
- [ ] R1: `SECURITY.md`（英語）。対象の版（最新の minor だけ）、報告の方法（GitHub の private vulnerability reporting。公開 issue に書かない）、返事と修正の目安（受領 7 日以内、high 以上は 30 日以内に修正版、公開は修正版のあと）、対象外（利用者が自分で入れた LLM・外部サービスの問題、ゲートウェイを通さない公開など README で禁止している構成）、報告者の表記。README の目次とセキュリティの節からリンクする。
- [ ] R2: リポジトリの設定で private vulnerability reporting を有効にする（**ユーザーの承認を得てから**、`gh api` で）。
- [ ] R3: コードスキャン: CodeQL のワークフロー（`python`、`javascript-typescript`〔frontend・extension・mcp 以外の JS〕、`actions`）を PR・main・毎週で実行し、結果を GitHub の code scanning に出す。既定のクエリセット。初回に出た high 以上はこの PR か直後の PR で直すか、誤検知なら理由つきで閉じる。
- [ ] R4: コンテナイメージのスキャン: CI で `biblioflow-api`（backend）と `biblioflow-frontend` のイメージをビルドし、compose に固定したイメージ（Caddy・pgvector・Valkey・cloudflared・tailscale）とあわせて既知の脆弱性を調べる。修正版がある high / critical で失敗。結果は SARIF で code scanning にも出す。毎週の定期実行でも回す（ベースイメージの新しい脆弱性に気づくため）。直せないものは理由と期限つきの許可リストに書く。
- [ ] R5: ワークフローの固定と検査: 全ワークフローの `uses:` をコミットの SHA に固定し、版をコメントで残す（Dependabot は SHA 固定のまま更新できる）。CI で `zizmor`（固定版）を全ワークフローに対して実行し、警告で失敗させる。各ジョブの `permissions` を最小にする（既定 `contents: read`、code scanning に出すジョブだけ `security-events: write`）。
- [ ] R6: ブラウザ拡張の監査: `audit.yml` に `extension` ジョブを足し、`extension/` に実行時の依存がないこと（`package.json` に `dependencies` がない、`npm ls` が空）と、配布 zip の中身が許可したファイルだけであることを確かめる。
- [ ] R7: `RELEASING.md` に「Security」の節: 報告を受けたときの手順（Draft security advisory → 非公開の fork で修正 → PATCH リリース → advisory の公開と CVE の申請）、リリース前のチェック項目に「code scanning と image scan に open の high 以上がない」を足す。基準 4 の状態を更新する。
- [ ] R8: 更新の通知（任意、既定オフ）: `.env` の `UPDATE_CHECK=true` のときだけ、API が 1 日 1 回 GitHub の最新リリースを取得し、新しい版があれば admin にだけ設定画面で知らせる（セキュリティ修正を含むかは changelog の `### Security` の有無で示す）。送るのは通常の HTTPS リクエストだけで、識別子や利用状況は送らない。取得は `safe_client` を通す。`install.sh` は聞かない（README に書く）。
- [ ] R9: CHANGELOG・STATUS.md・README（セキュリティの節）。

## スコープ外
- 外部の診断・ペネトレーションテスト、バグバウンティ（報奨金）。
- SBOM の生成・署名つきイメージの配布（イメージをレジストリに公開していないため。公開するときに検討）。
- OSSF Scorecard のワークフロー（R5 の範囲で同等の指摘は拾える。必要なら後で）。
- 既存のコードの網羅的なセキュリティレビュー（スキャンが出したものだけ直す）。
- 更新の自動適用（通知だけ。更新はこれまでどおり利用者が installer / `deploy.sh` で行う）。
- CodeQL の default setup（リポジトリ設定。ワークフローにしてレビューできるようにする）。

## 受け入れ基準
- `gh api repos/pathvector-studio/biblioflow/private-vulnerability-reporting` が `{"enabled":true}`。
- PR で CodeQL（3 言語）・image scan・zizmor・extension のジョブが動いて green。`gh api .../code-scanning/alerts?state=open&severity=high` と critical が 0 件。
- わざと古いベースイメージ（既知の critical を含む）にした一時ブランチで image scan が落ちることを確かめる（確かめたら消す）。`uses:` をタグに戻した一時の変更で zizmor が落ちることも同様。
- `grep -rhE '^\s*-?\s*uses:' .github/workflows | grep -vE '@[0-9a-f]{40}'` が空（ローカルの `./` を除く）。
- R8: 偽の GitHub API のサーバー（テスト内）で、新しい版があれば admin の設定画面にだけ出る・`UPDATE_CHECK` 未設定なら外へ 1 回もリクエストしない・プライベート IP の宛先は拒否される、をテストで確かめる。

## 決めたこと
- **新しく入るもの（依存の追加として承認が要る）**: `github/codeql-action`（init / analyze / upload-sarif）、イメージスキャナ、`zizmor`（`uvx` で固定版。新しい action は足さない）。新しいワークフローは `codeql.yml` と `image-scan.yml` の 2 つ。
- **イメージスキャナは Grype（Anchore、Apache-2.0）**: Trivy は 2026 年 3 月に配布物（action のタグとリリース）の改ざん事件があったため避ける。Grype はコンテナイメージを SHA で固定して `docker run` で使い（gitleaks と同じ方式。action は足さない）、脆弱性 DB の取得は実行時に行う。
- **失敗の条件は「修正版がある high 以上」**: 修正版のないものまで落とすと、ベースイメージの更新待ちで CI が止まり続けるため。修正版のない high 以上は code scanning に残して見えるようにする。
- **更新の通知は既定オフ**: 自己ホストの利用者に、黙って外へ通信させないため（embedding・LLM と同じ「設定すること自体が同意」の方針）。
- **SECURITY.md の目安（7 日・30 日）**: 個人の保守なので、守れる数字にする。
- 対象の版は最新の minor だけ（0.x の間は古い minor に修正を戻さない）。
