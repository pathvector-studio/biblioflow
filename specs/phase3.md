# Phase 3: PDF 解析・検索・AI 連携（承認済み 2026-11-05）

## 目的
論文の本文をページと座標つきで索引し、次の 2 つの入口から AI で使えるようにする。
- **MCP サーバー**: Claude Code などの MCP クライアントが、ライブラリの検索・本文の取得・注釈の作成を道具として使う。推論はクライアント側の LLM がやる。BiblioFlow 側に LLM の API キーは要らない。
- **アプリ内のチャット**（§3.3。Phase 4b の画面から使う）: BiblioFlow が LLM を呼ぶ。LLM の差し替えは LiteLLM で行い、Claude から始めて OpenAI / Gemini / Ollama などに順に広げる。

どちらも同じ検索と取得の API の上に作る。先に作るのは MCP（「まずは Claude Code」というユーザーの希望）。

## 要件
### 3a. 索引（LLM 不要）
- [ ] R1: `process_paper` で、本文をページ単位で抽出してチャンクに分ける。各チャンクに、ページ番号・見出し（取れれば）・本文・座標（ページ上の矩形の配列。PDF のポイント単位）を持たせる。抽出は `inspect_pdf_isolated` と同じ子プロセス方式で隔離する。
- [ ] R2: チャンクの embedding（1024 次元）を作って `paper_chunks.embedding` に保存し、`index_status` を `ready` / `failed` に遷移させる。embedding のモデルは設定で切り替える。既定は外部に送らないローカルモデル（下の「決めること 2」）。
- [ ] R3: 既存の論文を索引し直すコマンド（`python -m app.cli reindex [--all|--paper ID]`）。
- [ ] R4: 1 文字ずつ空白区切りの本文（ブラウザの「PDF に印刷」）を詰めてから分割する（#28 の `compact_letter_spacing` と同じ考え方）。

### 3b. 検索と取得の API（LLM 不要、PAT で使える）
- [ ] R5: `GET /api/v1/search?q=&workspace_id=&paper_id=&limit=`: 全文検索（`tsv`）とベクトル検索を RRF で合成したハイブリッド検索。返すのは、論文・ページ・座標・抜粋。権限は既存の `accessible_workspace_ids` で絞る。
- [ ] R6: `GET /api/v1/papers/{id}/chunks?page=`: ページ単位で本文と座標を返す（引用のジャンプ先に使う）。
- [ ] R7: スコープは既存の `read:papers` を使う。レートリミットは `RateLimiter` を使う。

### 3c. MCP サーバー
- [ ] R8: `biblioflow-mcp`（stdio。PAT と、必要なら Cloudflare Access のサービストークンを環境変数で受け取り、HTTP API を呼ぶ薄いクライアント）。リポジトリ内の `mcp/` に置き、`uvx` で起動できるようにする。
- [ ] R9: 道具は 5 つ: `search_library`、`get_paper`（書誌情報と BibTeX）、`read_pages`（本文と座標）、`list_annotations`、`add_note`（ノートへの追記。`write:papers` のときだけ）。
- [ ] R10: 道具の結果に入る論文の本文は、データであって命令ではないことを明示する（`<paper_context>` で区切る。§4.3）。

### 3d. アプリ内チャット（MCP の後）
- [ ] R11: LiteLLM 経由の `POST /api/v1/chat`（ストリーミング）。根拠のチャンクを `<paper_context>` で渡し、回答には `[p.4]` の形で引用を付けさせる。引用はチャンクの座標に対応づけて返す。
- [ ] R12: `POST /api/v1/papers/{id}/explain-region`: 矩形選択の画像を Vision 対応モデルに送って解説させる。
- [ ] R13: プロバイダーは `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` で切り替える。最初に Claude で確認し、OpenAI・Gemini・Ollama は LiteLLM の対応範囲で順に確認する。

## スコープ外
- OCR（画像だけの PDF）。必要になったら別に計画する。
- 画面（チャットタブ・引用ジャンプ）は Phase 4b。

## 決定事項（2026-11-05 ユーザー承認）
1. **進める順**: 3c の MCP をキーワード検索（`tsv`）だけで先に動かす → 3a の embedding を足して 3b をハイブリッド検索にする → 3d のアプリ内チャット。MCP と API の両方に対応する（API は Claude から始め、LiteLLM で他のプロバイダーに広げる）。
2. **embedding**: 既定はローカルの `BAAI/bge-m3`（1024 次元、外部に送らない、無料）。設定で Voyage `voyage-3.5` / OpenAI `text-embedding-3-large`（1024 次元）に切り替えられるようにする。切り替えたら R3 で索引し直す。
3. **PDF パーサ**: pypdf（BSD）で文字の座標を取る。PyMuPDF（AGPL）は使わない。

## 受け入れ基準
- backend の pytest がすべて通り、チャンク分割・ハイブリッド検索・権限・MCP の道具をテストする。
- 実際の論文（#28 の ScienceDirect の PDF を含む）で、検索結果の座標が PDF 上の正しい位置を指すことを Playwright で確かめる。
- MCP: Claude Code に `biblioflow-mcp` を登録し、Cloudflare Access の後ろにある本番から検索し、本文を読めることを確かめる。
