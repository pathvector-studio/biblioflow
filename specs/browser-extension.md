# ブラウザ拡張（ロードマップのトラック B）

> 状態: **承認待ち**（2026-11-08）。エクスポート・インポート（トラック C、`specs/export-import.md`）と並行で進める。

## 目的
出版社のサイト（ScienceDirect・IEEE・ACM・Springer・Wiley・arXiv など）で読んでいる論文を、1 クリックで BiblioFlow に保存する（Zotero Connector 相当）。サーバーから出版社のサイトを取りに行くと 403 で拒否されるので、利用者のブラウザが、利用者のログイン状態のまま PDF を取って送る。

## 要件
- [ ] R1: Chrome（Manifest V3）と Firefox で動く拡張を、このリポジトリの `extension/` に置く。ツールバーのボタンを押すと、今のページから見つけた論文（タイトル・著者・年・DOI / arXiv ID / PII）と、PDF が取れそうかを表示し、「保存」で送る。
- [ ] R2: 論文の情報は、ページのメタタグ（`citation_title`・`citation_doi`・`citation_arxiv_id`・`citation_pdf_url`・`dc.identifier`・`prism.doi` など）、なければ URL（arXiv・`doi.org`・ScienceDirect の PII）から読む。最低限、ScienceDirect・IEEE Xplore・ACM DL・SpringerLink・Wiley・arXiv・Nature・PLOS で識別子が取れる。
- [ ] R3: PDF が取れる場合（`citation_pdf_url` か、ブラウザで PDF を開いているタブ）は、ブラウザのログイン状態のまま PDF を取り、識別子を添えて `POST /api/v1/papers/upload` に送る。取れない場合（未購読・bot 対策のページ）は、識別子だけで `POST /api/v1/papers/metadata-only`（wishlist）に送り、そう表示する。
- [ ] R4: 保存先のワークスペースを選べる（前回の選択を覚える）。保存の結果（新規・既存と一致して PDF を紐付けた・失敗の理由）を表示し、BiblioFlow でその論文を開くリンクを出す。
- [ ] R5: 設定画面で、BiblioFlow の URL と PAT（`write:papers` と `read:papers`）を入れる。Cloudflare Access の内側にあるときは、ブラウザの Access のログイン（Cookie）でそのまま通る。サービストークン（`CF-Access-Client-Id` / `Secret`）も任意で入れられる。PAT とトークンは拡張のローカルの保存領域にだけ置き、ページのスクリプトには渡さない。
- [ ] R6: 拡張が送る先は、設定した BiblioFlow の URL だけ。ページの内容を読むのはボタンを押したときだけで、それ以外のときにページを読んだり、閲覧履歴を送ったりしない。権限は `activeTab`・`scripting`・`storage` と、設定した URL へのホスト権限（設定時に利用者が許可する）に絞る。
- [ ] R7: 文言は日本語と英語（ブラウザの言語に従う）。

## スコープ外
- Chrome Web Store・Firefox Add-ons への公開（公開は毎回ユーザーの承認が要る。まずは GitHub の Release に zip を置いて手で読み込む。公開は別の判断）
- Safari・モバイルのブラウザ
- 検索結果や目次のページから複数の論文をまとめて保存すること
- Web ページのスナップショット（論文以外の文献。ロードマップの D）
- bot 対策の回避（UA の偽装など）。取れない PDF は取れないと表示する
- サーバー側の変更（今の upload / metadata-only / workspaces の API で足りる）

## 受け入れ基準
1. Chrome と Firefox に `extension/` を読み込み、設定画面で開発環境の URL と PAT を入れると、「接続できた」と出る（PAT が間違っていればそう出る）。
2. arXiv の論文ページで保存すると、PDF つきで BiblioFlow に入り、識別子が arXiv ID になる。
3. ブラウザで購読しているか OA の出版社の論文ページ（例: PLOS、Nature の OA 記事）で保存すると、PDF つきで入る。購読していない論文では wishlist として入り、拡張が「PDF は取れなかった」と表示する。
4. すでに wishlist にある論文を、PDF の取れるページから保存すると、新しく作らず PDF が紐付く。
5. Cloudflare Access の内側の開発用ホストに、Access にログインしたブラウザから保存できる。
6. R2 のサイトごとに、保存したページの HTML（メタタグの部分だけ）を fixtures にして、識別子の抽出をテストする（CI で `node --test`）。manifest の検査も CI で行う。

## 決めたこと
- **置き場所はこのリポジトリの `extension/`**: API と同じリポジトリで、API の変更と同時に直せる。ライセンスは MIT のまま。
- **依存を足さない**: ビルドの道具を使わない素の JavaScript（ES modules）で書き、テストは Node の `node --test`。Chrome と Firefox の差は manifest だけにする（Firefox は `background.scripts`、Chrome は `service_worker`。CI で 2 つの zip を作る）。
- **認証は PAT**: 拡張からの Cookie 認証は CSRF の Origin 検査（`CORS_ALLOWED_ORIGINS`）と相性が悪い。PAT は Origin 検査の対象外で、スコープと期限を絞れる。
- **PDF の取得はブラウザ側で**: 出版社のサイトはサーバーからの取得を拒否するため（AGENTS.md の import-url の判断）。拡張は利用者のログイン状態のまま取るだけで、bot 対策は回避しない。取った PDF は既存の upload に送り、サーバー側の検査（マジックバイト・サイズ・子プロセスでの解析）をそのまま通す。
- **識別子の扱いはサーバーに任せる**: 拡張は見つけた DOI / arXiv ID / PII を `identifier` として添えるだけで、メタデータの解決と、wishlist との一致はサーバーの既存の処理が行う。
