# エクスポート・インポート（ロードマップのトラック C）

> 状態: **承認待ち**（2026-11-08）。ブラウザ拡張（トラック B、`specs/browser-extension.md`）と並行で進める。

## 目的
BiblioFlow に入れた文献を、LaTeX・Word・Obsidian・ほかの文献管理ソフトでそのまま使えるように書き出す。逆に、Zotero・Mendeley・BibTeX・RIS で管理してきたライブラリを、PDF・タグ・コレクション・ノートごと移せるようにする。今は 1 件ずつの BibTeX しか出せず、まとめて移す手段もないので、乗り換えの壁になっている。

## 要件
### エクスポート
- [ ] R1: 論文の一覧と同じ絞り込み（ワークスペース・コレクション・タグ・状態・年・`q`）で、BibTeX（`.bib`）・RIS（`.ris`）・CSL JSON（`.json`）のファイルをまとめて書き出せる。画面からはダウンロード、API からは同じ URL を PAT（`read:papers`）で取れる。
- [ ] R2: 書き出した BibTeX の citekey は、画面に出ている citekey と同じで、同じファイルの中で重複しない。RIS と CSL JSON にも、著者・タイトル・年・掲載誌・DOI・arXiv ID・アブストラクトが入る。
- [ ] R3: 自動エクスポート: ワークスペースごとに「絞り込み・形式・ファイル名」を登録すると、`data/exports/{workspace_slug}/{ファイル名}` にファイルが置かれ、論文を追加・編集・削除してから 2 分以内に書き直される（Better BibTeX の自動エクスポート相当。LaTeX や Obsidian がこのファイルを直接読める）。登録・削除は editor 以上。

### インポート
- [ ] R4: 画面から BibTeX・RIS・CSL JSON・Zotero RDF のファイル（4 MiB まで）を選ぶと、書誌情報だけを取り込める（PDF なしの `wishlist`）。取り込む前に「新規 N 件・既存と一致 M 件・読めなかった K 件」のプレビューを出し、確定してから保存する。
- [ ] R5: PDF つきの移行: Zotero の「ファイルもエクスポート」付きの書き出し（Zotero RDF か BibTeX と `files/` フォルダ）や、`file` 欄のある BibTeX / RIS を、サーバーの `data/import/{workspace_slug}/` に置いて画面から取り込みを始めると、PDF も既存の取り込み処理（`ingest_pdf`）で保存・索引される。進み具合と結果（件数と、失敗した項目の理由）を画面で見られる。
- [ ] R6: タグ（BibTeX の `keywords`、RIS の `KW`、RDF の `dc:subject`）はタグに、Zotero RDF のコレクションは同じ名前・同じ入れ子のコレクションに、ノート（RDF の `bib:Memo`、BibTeX の `note` / `annote`）は論文のノートの末尾に入る。
- [ ] R7: すでにある論文（同じワークスペースで DOI・arXiv ID・識別子が一致）は重複して作らない。既存の論文に PDF がなければ PDF を紐付け、タグ・コレクションは足す。タイトル・著者などの既存の値は上書きしない。
- [ ] R8: 取り込みは editor 以上。ファイルの中のパスは `data/import/{workspace_slug}/` の外を指せない（`..`・絶対パス・symlink を拒否）。PDF は既存どおり子プロセスで検査する。読めない項目があっても、ほかの項目は取り込む。

## スコープ外
- Zotero の SQLite（`zotero.sqlite`）を直接読むこと（形式が版ごとに変わる。Zotero の公式の書き出しを使う）
- Zotero の PDF 注釈（ハイライト）の移行、Mendeley Desktop の独自 DB（Mendeley Reference Manager は BibTeX / RIS で出せる）
- 引用スタイル（CSL）での参考文献の整形、Word / Google Docs 連携（ロードマップでは C の次）
- 本・章・学位論文などの文献の種類（今の論文モデルは論文だけ。取り込むときは種類を捨てて論文として入れ、BibTeX の元の項目は `bibtex` 欄に残す）
- ブラウザから PDF つきの zip をアップロードすること（大きなライブラリはアップロードの上限を超えるため、サーバーのフォルダに置く）
- BiblioFlow 独自の全体バックアップ形式（`scripts/backup.sh` がある）

## 受け入れ基準
1. コレクションを 1 つ選んで BibTeX を書き出すと、その論文だけが入り、`biber --tool` か `bibtex` で読めて、citekey が画面と一致する。RIS と CSL JSON も書き出せ、CSL JSON は CSL の JSON スキーマで検証が通る。
2. `curl -H "Authorization: Bearer bf_pat_…" "<host>/api/v1/export?format=bibtex&tag=<tag>"` で同じ中身が返る。`read:papers` のない PAT・ほかの人のワークスペースは 404 / 403。
3. 自動エクスポートを登録して論文のタイトルを変えると、2 分以内に `data/exports/…` のファイルが書き換わる。
4. 小さな Zotero ライブラリ（コレクションの入れ子・タグ・ノート・PDF を含むテスト用データ。リポジトリに fixtures として置く）を「ファイルもエクスポート」で Zotero RDF に書き出して `data/import/<slug>/` に置き、画面から取り込むと、件数・タグ・コレクション・ノート・PDF が一致する。同じものをもう一度取り込んでも重複しない。
5. BibTeX・RIS（Mendeley Reference Manager の書き出しを含む）・CSL JSON のファイルを画面から取り込むと、プレビューの件数どおりに wishlist として入る。
6. `file = {../../etc/passwd}` などの外を指すパスを含むファイルは、その項目だけが失敗として報告される。
7. バックエンドのテストが通り（新しい形式ごとのパーサとエクスポートの往復テストを含む）、CI が green。

## 決めたこと
- **書き出しの BibTeX は保存済みの `bibtex` 欄を使い、citekey だけ今の値に揃える**: 取り込み時やメタデータ解決時の元の項目（巻・ページなど、今のモデルにない欄）を失わないため。`bibtex` が空の論文は今の生成関数（`services/metadata/bibtex.py`）で作る。RIS・CSL JSON は DB の欄から作る。
- **自動エクスポートは worker の cron で書き直す**: 1 分ごとに、登録ごとの「最後に書いた時刻」より新しい変更（論文・タグ・コレクションの更新時刻、削除）があれば書き直す。書き込みは一時ファイルから rename して、読む側が途中のファイルを見ないようにする。置き場所は `data/library` と分けて `data/exports`（バックアップの対象に足す）。
- **PDF つきの移行はサーバーのフォルダから**: Watch Folder と同じ考え方（`data/import/{workspace_slug}/`）。数 GB のライブラリをブラウザで送らずに済み、セルフホストの利用者はサーバーにファイルを置ける。処理は Arq のジョブにして、1 件ずつ `ingest_pdf` を通す。
- **Zotero は公式の書き出し（Zotero RDF、または BibTeX + files）に対応する**: RDF はコレクションの入れ子とノートを持てる唯一の公式形式。
- **重複の判定は DOI・arXiv ID・識別子の一致だけ**: タイトルの類似での統合は誤りが怖いので、ロードマップの「重複の検出と統合」（D）に回す。
- **依存の追加（承認時に確認する）**: BibTeX の読み取りに `bibtexparser`（MIT）、RDF / XML の安全な読み取りに `defusedxml`（PSF）。RIS と CSL JSON は標準ライブラリで書く。どちらもライセンスの検査（`scripts/check-licenses.py`）を通る。
