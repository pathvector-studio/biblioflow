# 引用スタイル（CSL）での参考文献の出力（トラック D の 1 つ目）

> 状態: 実装済み（2026-11-12）

## 目的
v1 の基準 2（`RELEASING.md`）。論文を書くときに、選んだ論文・コレクション・タグから、APA や IEEE などの書式に整った参考文献の一覧をそのまま貼れるようにする。今は BibTeX / RIS / CSL JSON を書き出して、別のツール（LaTeX、Zotero、Word のプラグイン）で整形するしかない。

## 要件
- [x] R1: `GET /api/v1/bibliography?style=<id>&format=text|html` が、一覧・書き出しと同じ絞り込み（`papers.py` の `paper_filter`: workspace、collection、tag、status、year、q）と、`ids=<uuid>,...`（選んだ論文）で、整形済みの参考文献を返す。並びはスタイルの決まり（著者・年順か引用順。引用順のスタイルでは一覧の並び＝タイトル順を引用順とみなす）。権限は `accessible_workspace_ids` で絞り、PAT は `read:papers`。上限 1,000 件（超えたら 422 で絞り込みを促す）。
- [x] R2: 同梱のスタイルは 6 つ: APA 7th、IEEE、Chicago（author-date）、Nature、Vancouver、ACM（SIG Proceedings）。`GET /api/v1/bibliography/styles` が ID・表示名・種類（author-date / numeric）を返す。ロケールは `en-US` と `ja-JP`（スタイルが言語を固定していないときに、`locale=` で選べる。既定は `en-US`）。
- [x] R3: 整形は既存の CSL JSON の変換（`services/export.py` の `to_csl_items`）の結果を入力にし、Python の citeproc-py で行う。PDF の解析と同じく、細工された書誌（巨大な著者リストなど）で API が止まらないよう、子プロセスで時間とメモリを制限して実行する（インポートの解析と同じ方式）。
- [x] R4: 画面: ライブラリの書き出しメニューに「参考文献（スタイル）」を足し、スタイル・言語を選ぶと、今の絞り込み（または選んだ論文）の一覧をプレビューして、HTML（書式つきで Word / Google Docs に貼れる）とプレーンテキストでコピー、`.html` / `.txt` で保存できる。論文の画面の「書誌」タブにも、その 1 件の整形済みの引用を出す（スタイルは最後に選んだもの）。
- [x] R5: MCP に `format_bibliography`（論文 ID か検索条件とスタイル）を足す。返す文字列は書誌（論文由来）なので `<paper_context>` で囲む。
- [x] R6: 出力の正しさ: 6 スタイル × 代表的な 8 件（雑誌論文、会議論文、arXiv プレプリント、書籍、章、学位論文、著者 1 人・2 人・多数、日本語の著者名、年なし、DOI あり／なし）の期待値をテストに持つ。期待値は citeproc-js（Zotero と同じエンジン）で作り、citeproc-py の出力と比べる。citeproc-py が違う結果を出す点は、直せるものは前処理で直し、直せないものは差分と理由をテストに書いて許す。
- [x] R7: ドキュメントと帰属: README の節、`THIRD_PARTY_NOTICES.md`（citeproc-py は BSD-2-Clause、スタイルとロケールは CC BY-SA 3.0。同梱したファイルに元の著作権表示を残す）、CHANGELOG、`RELEASING.md` の基準 2 の状態、STATUS.md。

## スコープ外
- Word / Google Docs / LibreOffice のアドイン（文中の引用の挿入と、文書に合わせた自動更新）。v1 の条件ではない。
- 任意の CSL スタイルのアップロード・CSL リポジトリからの取得（6 つで始め、要望があれば次で）。
- 文中の引用（`(Smith, 2020)` / `[1]`）の生成、BibTeX → LaTeX の整形（LaTeX は BibTeX の書き出しで足りる）。
- 書誌の欠けを外部 API で補うこと（あるものだけで整形する）。

## 受け入れ基準
- `curl -H "Authorization: Bearer <PAT>" '<host>/api/v1/bibliography?style=apa&format=text&collection_id=<id>'` が、そのコレクションの APA の一覧を返し、権限のないワークスペースの論文 ID を `ids=` に入れても出てこない（404 ではなく除外。一覧 API と同じ挙動）。
- R6 のテストが通る（6 × 8 の期待値。許した差分はテストの中に理由つきで列挙される）。
- 画面で「参考文献」→ APA → 「HTML でコピー」をして、ブラウザの clipboard に `text/html` と `text/plain` の両方が入る（ヘッドレスブラウザで確かめる）。
- 1,000 件のライブラリで APA の生成が 10 秒以内。細工した 1 件（著者 10 万人）で、子プロセスが制限で止まり、API は 422 を返して動き続ける。
- `scripts/check-licenses.py` が通り、`THIRD_PARTY_NOTICES.md` に citeproc-py とスタイル・ロケールが載っている。

## 決めたこと
- **エンジンは citeproc-py（BSD-2-Clause、依存は lxml だけ、2026-11 時点で更新が続いている）**: citeproc-js は Zotero と同じで最も正確だが、ライセンスが CPAL / AGPL で、本番依存に入れられない（AGPL は禁止、CPAL も `check-licenses.py` の許可の外）。citeproc-js は**期待値を作る開発時の道具としてだけ**使い、リポジトリには入れない（作った期待値のファイルだけを入れる）。
- **依存の追加（ユーザー承認 2026-11-12）**: 本番に `citeproc-py`（と `lxml`。backend にまだなければ）。開発時の一回きりの道具として citeproc-js（npm から scratchpad に取得。コミットしない）。
- **スタイルは 6 つを同梱し、取得しない**: 外への通信を増やさないため。CSL のスタイル・ロケールは CC BY-SA 3.0 で、同梱するファイルだけがその条件に従う（コードの MIT には影響しない）。
- **整形は子プロセスで制限**: 書誌はインポートや他のメンバーから入るので、信用しない入力として扱う。
- 「引用順」のスタイル（IEEE・Nature・Vancouver・ACM）は、出力の番号が一覧の並び順になる。文中の引用がない以上、ほかに順序の根拠がないため。
- **実装時に決めたこと（2026-11-12）**:
  - citeproc-py は 0.12.1（0.12.2 は前日の公開だったため）。citeproc-js 2.4.63 で期待値を作った（`scripts/gen-csl-expected.mjs`、リポジトリの外の一時ディレクトリでだけ動く）。スタイルとロケールのコミットは `backend/app/services/csl/README.md`。
  - Vancouver は CSL のリポジトリに独立したファイルがないので、`nlm-citation-sequence`（NLM / Vancouver）を `vancouver` として出す。
  - ACM（`acm-sig-proceedings`）は番号つきだが、スタイル自身が著者順に並べる。一覧の並び（タイトル順）で番号を振るのは IEEE・Nature・Vancouver。
  - citeproc-py の不具合を 2 つ補った: 名前の `initialize="false"` を無視する（Chicago が頭文字にする）のを直し、HTML で値をエスケープしないので、整形の前にすべての文字列をエスケープする（`<script>` やリンクの属性を壊す値が素通りしないことをテストと 6 スタイルで確認。どのスタイルもリンク〔`<a>`〕は出さない）。
  - citeproc-js との差は、直せるものは出力の後処理で直し、残る 44 件（APA の "(eds.)" の大文字、学位論文の余分な "(Publication)"、日本語の「と」「ほか」の前後の空白、ja-JP での Chicago のタイトルの大文字化、CJK の斜体）をテストに理由つきで列挙した。新しい差や、消えた差があればテストが落ちる。
  - 子プロセスのメモリ上限は 512 MiB（普通の要求は 100 MiB 未満）。著者 10 万人の 1 件は、citeproc-py が先頭の数人しか出さないので 0.3 秒・110 MiB で終わり、制限には当たらない（受け入れ基準の「止まる」は、上限を下げたテストと、時間の上限で止める例〔同じ著者・同じ年の 1,000 件の Chicago〕で確かめた。どちらも 422 を返し、次の要求は処理される）。1,000 件の APA は約 3.7 秒。
  - レートリミットは 30 回/分（書き出しの 10 回より多い。ダイアログがスタイル・言語を変えるたびに取り直すため）。
  - HTML の応答には `Content-Security-Policy: default-src 'none'; sandbox` を付ける。
  - 画面の確認（2026-11-12、ヘッドレスの Chromium）: ダイアログで IEEE を選んでプレビューし、「HTML でコピー」でクリップボードに `text/html` と `text/plain` の両方が入ること、論文の「書誌」タブに最後に選んだスタイルの引用が出ることを確かめた。Word / Google Docs に実際に貼る確認はしていない。
