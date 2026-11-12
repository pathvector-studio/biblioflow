# コミュニティのファイル・README・Sponsors

> 状態: 承認済み（2026-11-12）。親セッションが実装する

## 目的
外の人が使い始め、問題を報告し、PR を出せるようにする（v1 の基準 5「保守者以外の利用者 3 人以上」の前提）。今は CONTRIBUTING も issue / PR のテンプレートもなく、README は機能の説明が長く、最初の 1 画面で何ができるのかが伝わらない。リポジトリの説明文は古い（「AI チャットは今後」）。

## 要件
- [ ] R1: `CONTRIBUTING.md`（英語）: 開発環境の立ち上げ（`docker compose`、テストの実行、frontend / mcp / 拡張のテスト）、PR の約束（1 PR 1 つの変更、CHANGELOG の `[Unreleased]` に利用者向けの 1〜3 行、テストを足す、`main` へのマージは CI green と保守者の承認）、新しい依存は先に issue で相談、ライセンス（MIT、出した変更は MIT で受け入れる）、公開リポジトリに個人の情報を書かない（`scripts/check-private-info.py` と git フックの設定）、セキュリティの報告は SECURITY.md へ。
- [ ] R2: `CODE_OF_CONDUCT.md`: Contributor Covenant 2.1 を採用すると書き、本文は公式のページへのリンクにする（全文は転載しない）。連絡先は GitHub の保守者への非公開の連絡（Security と同じく private な経路。メールアドレスは書かない）。
- [ ] R3: issue のテンプレート（`.github/ISSUE_TEMPLATE/` の YAML フォーム）: 不具合（版、公開形態、手順、期待と実際、ログの出し方と「秘密を貼らない」注意）、機能の提案（困っていること、Zotero との差なら roadmap の表へのリンク）。`config.yml` で空の issue を禁止し、脆弱性は SECURITY.md へ、質問は Discussions へ誘導する。
- [ ] R4: PR のテンプレート: 何を・なぜ、確かめたこと（テスト・手での確認・「未確認」の明記）、CHANGELOG を足したか（または `skip-changelog` の理由）、個人の情報を入れていないこと。
- [ ] R5: README の刷新: 冒頭を「何か・誰向けか・3 行で入れる方法（install.sh）・スクリーンショット 2 枚（ライブラリ、PDF＋AI タブ）」にし、機能を短い表にまとめ、細かい設定（公開形態、MCP、AI、拡張、バックアップ）は今の節を残して目次から辿れるようにする。スクリーンショットは開発用ではない一時インスタンス（偽のデータ、オープンアクセスの論文）をヘッドレスブラウザで撮り、`docs/images/` に置く（個人の情報・ホスト名・メールが写っていないことを確かめる）。
- [ ] R6: Sponsors: README の「Sponsoring」節に、寄付が何に使われるか（保守、検証用の環境、ホスティング版の準備）を 2〜3 行で書く。GitHub Sponsors の tier の文案（月 3・10・50 ドル程度と 1 回だけ）を `specs/community.md` の末尾に用意する。**Sponsors の設定そのものはユーザーが行う。**
- [ ] R7: リポジトリの説明文とトピックの文案を用意し、ユーザーの承認を得てから `gh api` で更新する（リポジトリの設定なので毎回承認）。Discussions は有効にする（ユーザー承認 2026-11-12。community の PR をマージするときに `gh api` で）。
- [ ] R8: CHANGELOG（README の刷新を 1 行）・STATUS.md。

## スコープ外
- ドキュメントサイト（biblioflow-web の変更。必要なら別の計画）、翻訳した README。
- GOVERNANCE・MAINTAINERS・CLA / DCO（保守者 1 人のうちは不要）。
- Sponsors の tier の設定、Discussions のカテゴリの作り込み、ラベルの整理。
- README の内容の大きな書き直し（構成を変え、冒頭を足し、古い記述を直すだけ）。

## 受け入れ基準
- GitHub の「Community Standards」（Insights → Community）で、Description・README・Code of conduct・Contributing・License・Security policy・Issue templates・Pull request template がすべて満たされている。
- 新しい issue の画面に 2 つのフォームと誘導（SECURITY.md、Discussions を有効にした場合はそれも）が出て、空の issue は作れない（`gh api repos/:owner/:repo/contents/.github/ISSUE_TEMPLATE/config.yml` と画面の確認）。
- README の冒頭 1 画面に、説明・インストールの 3 行・スクリーンショットが入る。README のリンクがすべて生きている（相対リンクと見出しのアンカーを検査するスクリプトで確かめる。外部の URL は HTTP 200）。
- スクリーンショットに LAN の IP・ホスト名・個人のメールが写っていない（目視と、画像を作ったインスタンスのデータが偽物であること）。`scripts/check-private-info.py` が通る。

## 決めたこと
- **Code of Conduct は全文を転載せず、採用とリンクだけ**: 本文は外部の著作物で、版の更新にも追従しやすい。
- **連絡先にメールを書かない**: 公開リポジトリに個人のメールを書かない規則があるため。GitHub の非公開の経路を使う。
- **スクリーンショットは一時インスタンスで撮る**: 開発環境・本番には実データと手元の情報があるため。
- tier の金額は文案で、決めるのはユーザー。

## 付録: GitHub Sponsors の tier の文案（設定はユーザーが行う）
- **$3 / month — Reader**: "Thank you for keeping BiblioFlow going. Your name in the sponsors list on the README (if you want it)."
- **$10 / month — Lab member**: "For people who use BiblioFlow every day. Everything in Reader, and your feature requests get a reply within a week."
- **$50 / month — Lab**: "For a lab that runs BiblioFlow for its members. Everything in Lab member, your lab's name or logo in the README, and help with your setup by e-mail (deployment mode, migration from Zotero) once a quarter."
- **One-time — Coffee ($5)**: "A one-time thank-you."

Sponsorships pay for maintenance and security fixes, the machines and services used to test releases, and the work towards a hosted version. Rewards stay small on purpose: the maintainer is one person.
