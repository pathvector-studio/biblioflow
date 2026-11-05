# 埋め込みモデルの配布と同意（承認済み 2026-11-06）

## 目的
3a/3b で入れたローカルの bge-m3 のせいで、インストールが重くなった。
- fp32 の 2.3 GB を落としてから、手元で int8 に変換している。
- 変換のメモリが約 9 GiB まで上がる。
- `docker compose up --wait` が、モデルがそろうまで終わらない。`install.sh` の 300 秒を超えると失敗する。

これを次の 3 点で軽くする。
1. 量子化済みのモデルを配布する。
2. モデルの取得を起動から切り離す。
3. モデルの取得には利用者の同意を取る（ユーザーの指示 2026-11-06）。同意は専用の変数を作らず、インストール時の yes/no で `EMBEDDING_PROVIDER` の初期値を決める形にする（ユーザー案 2026-11-06）。

## 要件
### A. 量子化済みモデルの配布
- [ ] A1: GitHub Actions のワークフロー `model-artifact.yml`（手動実行）で int8 のモデルを作って公開する。
  - 公式 `BAAI/bge-m3` の固定リビジョンを `spec.py` の sha256 で検証してから、`quantize_dynamic` で int8 にする（ランナーは 16 GB）。
  - このリポジトリの GitHub Release `model-bge-m3-int8-<rev7>-<n>` に資産として置く: `model_int8.onnx`（約 0.57 GB）、`tokenizer.json`、bge-m3 の `LICENSE`（MIT）、`SHA256SUMS`、作り方の説明。
  - `actions/attest-build-provenance` で、CI が作ったことの証明を付ける。
- [ ] A2: 再現性の確認。同じワークフローの中で 2 回量子化し、sha256 が同じになるかを記録する。
  - 同じなら、手元で作り直した人がバイト単位で照合できる。
  - 違うなら、ベクトルの一致（コサイン 0.999 以上）を確かめる手順を README に書く。
- [ ] A3: `spec.py` に、配布物の URL・サイズ・sha256 を固定する。
  - model-init は int8 なら Release から約 0.6 GB を落として検証するだけになり、量子化しない。
  - fp32 は今までどおり Hugging Face の公式から落とす（量子化はない）。
  - 本番の依存から `onnx` パッケージを外す（量子化はワークフローの中だけで使う）。
  - `MODEL_INIT_MEM_LIMIT` の既定は 512m に下げる。
- [ ] A4: `embedding_model` の識別子は、配布物の sha256 が今の手元の量子化と同じなら変えない。違うなら新しい識別子（例 `bge-m3@5617a9f:int8r1`）にする。そのときは C2 の自動の埋め直しで作り直される。

### B. 起動から切り離す
- [ ] B1: embedder は model-init を待たない（`depends_on` をやめる）。
  - モデルがなくても起動して healthy になる。
  - モデルのディレクトリに「検証済み」の印（model-init が最後に書く `READY`。モデル ID を中に書く）が現れたら読み込む。
  - それまでの `/embed` は 503（`model not ready`）を返す。
  - `/healthz` はモデルの状態（`absent` / `loading` / `ready`）を返す。
- [ ] B2: `docker compose up --wait` は、モデルの取得を待たずに終わる。model-init は裏で走って終わる。`install.sh` のタイムアウトに関わらなくなる。
- [ ] B3: モデルがない間も、検索はキーワードだけで答える（今の degraded と同じ）。

### C. 索引と状態
- [ ] C1: 論文の `index_status` は、チャンクが入った時点で `ready` にする。
  - embedding は別に追いかける。今の「embedding がそろうまで `processing`」はやめる。モデルが来ない限り、ずっと `processing` のままになるため。
  - `embed_paper` は、embedder が `model not ready` のときは再試行を使い切らず、何もしないで終わる。
- [ ] C2: 自動の埋め直し。worker の cron（10 分ごと）で、次の両方を満たすとき、該当する論文の `embed_paper` を積む（重複は Arq の job id で防ぐ）。
  - embedder が `ready` である。
  - 今のモデルの embedding がないチャンクがある。
  これで、同意して取得が終わったときも、本番を更新したときも、`reindex --embeddings-only --missing` を手で打たなくてよくなる（コマンド自体は残す）。
- [ ] C3: `GET /api/v1/search/status`（ログインしていれば誰でも見られる）。返すのは次の 4 つ。
  - provider
  - モデルの状態（`disabled`〔`none`〕 / `absent` / `downloading` / `ready`）
  - embedding のあるチャンク数と全体のチャンク数（自分が見られるワークスペースの分）
  - degraded かどうか
  MCP の `search_library` が degraded を伝えるときにも使う。

### D. 同意（= プロバイダーの初期値）
- [ ] D1: 同意のための専用の変数は作らない。`EMBEDDING_PROVIDER=local` にすること自体を同意とみなす。
  - `.env.example`・compose（`${EMBEDDING_PROVIDER:-none}`）・`config.py` の既定は **`none`** にする。何もしなければ何も落とさず、キーワード検索だけで動く。
  - model-init は `local` のときだけ取得する。`none` のときはログに、`local` にすると何をどこから落とすか・大きさ・ライセンスを出して終わる。
- [ ] D2: `install.sh` で聞き、答えで `.env` の `EMBEDDING_PROVIDER` を決める（yes なら `local`、no なら `none`）。
  - 聞く前に次の内容を表示し、`[y/N]` で答えてもらう。
    - 何を: bge-m3 の int8（約 0.6 GB）
    - どこから: このリポジトリの GitHub Release
    - ライセンス: MIT
    - 本文を外に送らないこと
    - あとから `.env` で変えられること（fp32 なら Hugging Face から 2.3 GB）
  - `curl | bash` でも聞けるように `/dev/tty` から読む。
  - TTY がなければ、`BIBLIOFLOW_SEMANTIC_SEARCH=yes` のときだけ `local` にし、それ以外は `none` にする。
- [ ] D3: `none` のとき、ライブラリの検索欄で admin にだけ「意味検索は無効です」と、有効にする手順を出す。手順は、`.env` に `EMBEDDING_PROVIDER=local` を書いて `docker compose up -d`。
- [ ] D4: 外部プロバイダー（voyage / openai）を選ぶ人には、本文を外に送ることを `.env.example` と README で説明する（今と同じ）。
- [ ] D5: 既存の環境の扱い。
  - dev と本番の `.env` には `EMBEDDING_PROVIDER` がないので、更新すると `none` になる。
  - dev はユーザーに確認してから `local` を書く。
  - 本番は「本番更新して」のときに確認する。

## スコープ外
- 画面から同意のボタンを押して取得を始めること。API に設定を書き換える権限、または model-init を起動する権限を持たせることになるため。必要になったら別に計画する。
- ビルド済みイメージの GHCR 配布（公開版の配布のときに別に計画する）。
- 小さいモデルへの切り替え（`vector(1024)` の固定と、日本語の質のため）。

## 受け入れ基準
- backend / mcp のテストがすべて通る。CI の compose ジョブは、既定の `none` のまま起動して healthy になる（`.env` を書き換える sed は要らなくなる）。
- ワークフローで Release に資産が置かれ、`SHA256SUMS` と証明が付き、再現性の結果が記録される。
- 空の `models` ボリュームで次の 3 つを確かめる。
  - `none`（既定）: 何も落とさず、`up --wait` が数十秒で終わり、キーワード検索が使える。
  - `local`: 裏で 0.6 GB を落として検証し、embedder が ready になる。
  - 自動の埋め直し: 手で reindex しなくても、全チャンクに embedding が付く。
- 取得中のメモリの最大（model-init と embedder）を記録する。量子化の山がなくなっていること。
- `install.sh` の同意の質問を、TTY ありとなしで確かめる。
