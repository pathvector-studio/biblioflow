# ロードマップ

> 最終更新: 2026-11-05。順番と範囲はユーザーと合意済み。各項目は着手時に `specs/<name>.md` で要件を書いて承認をもらう。

## 目標
1. **AI 連携は MCP と API の両方**: MCP サーバー（Claude Code などのクライアント側 LLM が推論する。API キー不要）と、アプリ内で LLM を呼ぶチャット（LiteLLM で多プロバイダー）。MCP が先。
2. **出版社の網羅**: ScienceDirect / arXiv / DOI 付き PDF だけでなく、IEEE・ACM・Springer・Wiley などからも、書誌情報と PDF を手間なく取り込めるようにする。サーバーから出版社サイトを取りに行く方式（import-url）は 403 で拒否されるので、ブラウザ側から送る。
3. **Zotero と同等以上**: Zotero でできて BiblioFlow でできないことをなくす（下の機能差の表で管理する）。

## 進め方（並行トラック）
| トラック | 内容 | 状態 |
|---|---|---|
| A. Phase 3（AI） | `specs/phase3.md`。3c MCP（キーワード検索）→ 3a embedding（bge-m3 既定）+ 3b ハイブリッド検索 → 3d アプリ内チャット・矩形選択の Vision 解説 → Phase 4b の画面 | 次に着手 |
| B. ブラウザ拡張 | 出版社のページから 1 クリックで保存。ページの DOI / arXiv / PII（`citation_*` メタタグ等）を拾って既存の API（PAT + Access のサービストークン）に送る。PDF はブラウザのセッションで取得して upload に送る。ブラウザが開いている PDF をそのまま送る経路も作る | A と並行 |
| C. エクスポート・インポート | BibTeX / RIS / CSL JSON の一括出力（コレクション・タグ・検索結果単位）と、自動更新される出力ファイル（Better BibTeX 相当）。Zotero（RDF / SQLite + storage）・Mendeley・BibTeX / RIS からの移行（添付 PDF・タグ・コレクション・ノートを含む） | A と並行 |
| D. 残りの機能差 | 下の表の「中」「低」を順に | A〜C の後 |

## Zotero との機能差
BiblioFlow にすでにあるもの: Web だけで完結、ファイル名の規則、コレクション（入れ子）、タグ、読書状態、ノート、ハイライトと矩形の注釈、共有ワークスペース、PAT と API、Watch Folder、論文ごとの BibTeX、Cloudflare Access 前提の公開。

| 重要度 | 機能 | トラック |
|---|---|---|
| 高 | ブラウザ拡張（Zotero Connector 相当）で出版社のページから保存 | B |
| 高 | BibTeX / RIS / CSL JSON の一括エクスポート、自動エクスポート | C |
| 高 | Zotero・Mendeley・BibTeX・RIS からのインポート | C |
| 高 | 引用スタイル（CSL）での参考文献の生成、Word / Google Docs / LibreOffice との連携 | D（C の後すぐ） |
| 高 | PDF 以外の文献: 本（ISBN 検索）、章、学位論文、Web ページのスナップショット、EPUB | D |
| 中 | 重複の検出と統合 | D |
| 中 | 全文検索 | A（3b） |
| 中 | オープンアクセスの PDF の自動取得（Unpaywall） | D |
| 中 | ゴミ箱と復元、保存した検索、関連文献のリンク | D |
| 中 | 注釈の種類（手書き・下線）、注釈をノートへ書き出す | D |
| 低 | モバイル対応、RSS フィード、撤回論文の警告、プラグインの仕組み | D |
