# STATUS

親セッションだけが更新する（体制は `specs/agent-workflow.md`）。最終更新: 2026-11-13

## 進行中
- 2026-11-13 から開発ループ（`/loop /dev-loop`、`LOOP.md`）に切り替え。次は v1-compat（v1 の基準 3）の spec、続いて pypdf 7・bibtexparser 2 の spec、dist-clients（基準 6 の準備）の spec

## 承認待ち（ユーザー）
- v0.3.0 のタグ・GitHub Release・本番の更新・本番のバックアップの cron。タグ前のチェック（新規インストール・v0.2.0 からのアップグレード・`restore.sh --check`）は 2026-11-12 に通った。タグは #66 のマージ（1a46a61）。それ以降の変更は `[Unreleased]`（次の 0.4.0）
- CODE_OF_CONDUCT.md の連絡先（専用のアドレス。個人のメールは書かない）。文面は用意済み
- GitHub Sponsors の tier（文案は `specs/community.md` の末尾）
- Dependabot のメジャー更新 pypdf 7（#61）・bibtexparser 2（#62）: ループが spec を書くので、その承認
- Dependabot の setup-uv 7 → 10（#81、Actions のメジャー更新）

## 予定
- v1.0 の基準（`RELEASING.md`）: 1 移行・2 CSL・4 セキュリティは完了（2026-11-12）。残りは 3 互換性の約束（OpenAPI の公開と、前のタグからのアップグレードを CI で）、5 本番での常用（4 週間・実際の復元・外の利用者 3 人）、6 クライアントの配布（Chrome Web Store・AMO・PyPI。公開は毎回ユーザーの承認）
- 未確認の検証（ユーザーの操作が要る）: ブラウザ拡張（Firefox での実動作、実際の権限のダイアログ、出版社のサイトでの PDF の取得と保存、ScienceDirect、Cloudflare Access の内側）、CSL の出力を Word / Google Docs に実際に貼ること、長く使われた大きな Zotero のライブラリ
- 依存関係: メジャー更新（TypeScript 7、tailwind-merge 3、Tailwind 4）と mcp SDK 2.x は v1 の基準を終えてから 1 つずつ spec を書く（ユーザー判断 2026-11-12。`dependabot.yml` の ignore に記載）
- biblioflow-mcp を外部の MCP カタログ（HOL Guard）に登録する: v1.0 になってから（PyPI に出すのが前提）

## 詰まり
- なし（Remote Control 経由では子セッションを起動できないので、子は subagent を既定にした）

## 直近の完了
- 選んだ文章の翻訳（#76。ヘッドレスの Chromium と Ollama で、選択 → 翻訳 → 日本語 → ノートに追加まで確認）
- CodeQL の指摘: ScienceDirect のホストの判定を厳密に（#75）。残り 3 件は理由を書いて閉じた（誤検知 2・意図どおり 1）
- 引用スタイル（CSL）での参考文献（#74、v1 の基準 2）。citeproc-py、6 スタイル、citeproc-js との照合
- Zotero 7 以降の RDF の取り込み（#73、v1 の基準 1）。Zotero 10.0.9 の実際の書き出しで PDF が付かず DOI が落ちる不具合を見つけて直した
- コミュニティのファイル・README の刷新（#72）、Discussions の有効化、説明文とトピックの更新
- 更新の通知（#71、`UPDATE_CHECK`、既定オフ）
- セキュリティの基盤（v1 の基準 4）: SECURITY.md（#65）、private vulnerability reporting、Actions の SHA 固定と zizmor（#66）、CodeQL とイメージの検査（#68）、拡張の監査（#69）。Caddy 2.11.7 の悪用済みの脆弱性は 2.11.9 で解消
- v0.3.0 のリリース PR（#54）とインストーラのメッセージ（#63）
- v1.0 の基準を `RELEASING.md` に書く
- 拡張が実際の IEEE Xplore・ACM DL のページを読めるようにする（#51。arXiv・PLOS・Nature・Springer・Wiley も実ページで抽出を確認）
- BibTeX の書き出しを `biber` / `bibtex` で読める形にする（#50。CSL JSON は公式スキーマを通過）
- 書誌ファイルの解析を制限つきの子プロセスに移す。細工したファイルによる API の停止を防ぐ。拡張は URL を変えたらトークンを再入力させる（#49）
- frontend の minor・patch（#22）
- 論文の画面の編集可否を、ヘッダーのワークスペースではなく論文のワークスペースで決める（#47）
- B・C の締めのドキュメント（#46）
- C 完了: エクスポート・インポート（#41 まとめて書き出し、#43 自動エクスポート、#44 ファイルからの取り込み、#45 サーバーのフォルダからの PDF つきの移行）
- B 完了: ブラウザ拡張（#42。Chromium で確認、Firefox と実際の出版社のサイトは未確認）
- 4b 完了: AI タブ・引用ジャンプ・会話（#36）、横断の AI 画面（#37）、矩形選択の AI 解説（#38）
- 3d 完了（#27〜#34）。R13 は Ollama のみで完了とし、Claude での確認は行わない（費用のため。ユーザー判断 2026-11-07）
- LLM の設定が足りないときは起動を止めず、警告してチャットを無効にする（ユーザー判断 2026-11-07）
- 3d の R13: Ollama（qwen2.5vl:7b）で 3 つの API を確認
- 3d の 6: ドキュメント（#32）
- 3d の 5: 矩形選択の Vision 解説、Pillow 追加（#31）
- 3d の 4: 横断チャット（#30）
- 3d の 3: 単一論文のチャット（#29）
- 3d の 2: 会話の保存（#28）
- 3d の 1: LLM 層と設定（#27）
- 3d-1: LLM 層と設定（#27）
- 3d の spec（#26）
- PDF の SHA-256 を chunks・詳細 API と MCP で返す（#25）
- 開発体制の試行 1 回目（biblioflow #23・biblioflow-web #4）。ユーザーの中継 0 回
- v0.2.0 リリース（#21）
