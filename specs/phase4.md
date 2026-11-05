# Phase 4: Frontend Web UI（AI 以外）

> 状態: **実装済み**（2026-11-03）。受け入れ基準は Playwright（headless Chromium）で LAN アドレス経由に確認。
> AI 関連（チャット、引用バッジでのジャンプ、矩形選択した図の Vision 解説、横断検索チャット）は Phase 3 のあとに Phase 4b として追加する。

## 目的
今は Swagger UI でしか操作できず、ログイン画面すらない。ブラウザだけで「ログイン → 論文を探す・追加する → PDF を読んでハイライト・メモする → 書誌/BibTeX を使う」までを完結させる（AGENTS.md §1-1、§3.3 の AI 以外の部分、§5 Phase 4）。LAN 内の別 PC（`http://<host>:3080`）から使えることを前提にする。

## 要件
### 共通
- [x] R1: 表示言語を日本語と英語で切り替えられる。初回はブラウザの言語に合わせ、選んだ言語はブラウザに保存されて次回も維持される。すべての画面の文言が両言語で用意されている（未翻訳のキーがない）。
- [x] R2: 未ログインでどのページを開いてもログイン画面に移り、ログインすると元のページに戻る。ログアウトするとセッションが失効し、戻るボタンでも保護ページが見えない。
- [x] R3: ヘッダーでワークスペース（個人 + 所属している共有）を切り替えられ、一覧・追加・コレクションは選んだワークスペースが対象になる。選択はリロードしても保持される。
- [x] R4: API エラー（401/403/404/409/413/415/422/429/5xx）は画面に分かる言葉で表示され、操作が無言で失敗しない。401 のときはログイン画面に移る。
- [x] R5: 幅 1280px 以上のデスクトップで全機能が使える。幅 768px 程度でも一覧と閲覧は崩れずに使える。ライト/ダークはOS 設定に従う。

### 認証・アカウント
- [x] R6: メールとパスワードでログインできる。誤りやレートリミット（429）のときはメッセージを出す。
- [x] R7: 設定画面で自分のパスワードを変更できる（現在のパスワードが必要。新しいパスワードは既存のパスワードポリシーで検証する）。変更後も今のセッションは続き、それ以外の手段では古いパスワードでログインできない。
- [x] R8: 設定画面で PAT を発行・一覧・失効できる。発行時は名前・スコープ（read / write）・有効期限を選べ、トークン本体は一度だけ表示されてコピーできる。一覧には名前・先頭部分・スコープ・期限・最終使用日時が出る。

### ライブラリ（一覧）
- [x] R9: 論文一覧に、タイトル・著者・年・掲載誌・タグ・状態・PDF の有無が出る。キーワード（タイトル/著者/識別子/citekey）、状態（unread/reading/read/wishlist）、年、タグ、コレクションで絞り込める。入力から結果の表示まで 1 秒以内（ローカルで 1,000 件のとき）。件数が表示され、ページ送りできる。
- [x] R10: 一覧から状態を直接変えられる（例: wishlist → reading）。Wishlist は専用のタブで見られる。
- [x] R11: 論文を選択して citekey / BibTeX をクリップボードにコピーできる（複数選択時は BibTeX を連結してコピー）。

### 論文の追加
- [x] R12: PDF をドラッグ&ドロップまたはファイル選択でアップロードできる（複数ファイル可）。ファイルごとに進捗と結果（登録された論文へのリンク / 重複 409 / 不正ファイル 415 / 大きすぎる 413）が出る。
- [x] R13: DOI / arXiv ID / ScienceDirect の URL・PII / タイトルを入力すると、外部メタデータの候補（タイトル・著者・年・掲載誌）がプレビューされ、選んだ候補を Wishlist として登録できる。タイトルで候補がないときは手入力（タイトル・著者・年）で登録できる。
- [x] R14: Wishlist の論文の画面から PDF をアップロードすると、その論文に紐付く（新しい論文が増えない）。

### 論文の閲覧（2ペイン）
- [x] R15: 左ペインに PDF ビューア（ページ送り、ズーム、ページ番号へのジャンプ、テキスト選択）。50 ページ程度の PDF が数秒で表示され、スクロールが引っかからない。PDF がない論文では代わりにアップロード欄を出す。
- [x] R16: テキストを選択してハイライトを作れる。色とコメントを付けられ、あとから編集・削除できる。リロードしても同じ位置に表示される。
- [x] R17: マウスのドラッグで矩形領域（図・表・数式など）を選択して「エリア注釈」として保存でき、コメントを付けられる。Phase 4b で AI 解説を呼ぶための入口になる。
- [x] R18: 右ペインはタブで「ノート / 書誌 / 注釈一覧」を切り替える。（AI タブは Phase 4b で追加）
  - ノート: Markdown で書けてプレビューでき、自動保存される（保存状態が見える）。HTML はサニタイズされる。
  - 書誌: タイトル・著者・年・掲載誌・アブストラクト・状態・タグ・コレクション・citekey を編集でき、BibTeX を表示・コピーできる。PDF をダウンロードできる。
  - 注釈一覧: ページ順に並び、クリックで PDF の該当位置へスクロールしてハイライト表示する。
- [x] R19: viewer 権限のワークスペースでは、編集系の操作（追加・編集・削除・注釈作成）が出ないか無効になっている。

### コレクション・削除
- [x] R20: サイドバーでコレクション（入れ子可）を作成・名前変更・削除でき、クリックで一覧を絞り込める。論文の書誌タブからコレクションに出し入れできる。コレクションを削除しても論文は消えない。
- [x] R21: 論文を確認ダイアログつきで削除できる。削除すると注釈・タグ付け・チャンクも消える。PDF ファイルは、他のワークスペースの論文が同じファイルを参照していなければライブラリからも消える。

### バックエンド（上記に必要な API の追加）
- [x] R22: 注釈の一覧・作成・更新・削除 API（論文単位。閲覧は viewer、作成・変更は editor。他人の注釈の変更・削除は owner と本人のみ）。
- [x] R23: タグ一覧 API（ワークスペース単位、件数つき）、コレクションの一覧・作成・更新・削除 API、論文の削除 API、パスワード変更 API。すべて既存の RBAC ヘルパーを通し、権限がないものは 404 を返す。
- [x] R24: 追加した API には統合テストがあり、既存テストと合わせて全件通る（skip 0）。

## スコープ外
- AI チャット、引用バッジでのジャンプ、矩形選択画像の AI 解説、ライブラリ横断の検索チャット（Phase 3 → Phase 4b）
- 共有ワークスペースのメンバー管理画面（作成・メンバー追加・権限変更）。共有ワークスペースの切り替えと利用だけはできる。メンバーは当面 API で追加する
- URL からの PDF 取り込み画面（機能自体が既定で無効）
- Watch Folder の状態表示・`_rejected` の閲覧
- ユーザー登録画面・管理者画面・パスワードリセット（メール送信）
- 論文の一括操作（複数選択での状態変更・削除・タグ付け）。複数選択は BibTeX コピーだけ
- オフライン対応、PWA、モバイル（幅 768px 未満）専用レイアウト
- 注釈のリアルタイム共同編集・他ユーザー注釈の表示切り替え
- 英語・日本語以外の言語

## 受け入れ基準
すべて LAN 内の別 PC のブラウザから LAN のアドレス（ポート 3080）で行う。
1. `cd frontend && npm run lint && npm run typecheck && npm run build` が通る（`lint` / `typecheck` スクリプトは今回追加する）。CI（backend pytest skip 0、frontend build、compose スモーク）が green。
2. 未ログインで `/library` を開く → ログイン画面 → テスト用ユーザーでログイン → `/library` に戻る（R2, R6）。
3. 言語を英語に切り替えてリロード → 英語のまま。日本語に戻す（R1）。
4. 一覧で「residual」を検索 → ResNet が出る。状態を wishlist で絞り込む → ScienceDirect の論文だけ出る（R9, R10）。
5. 追加画面で、まだ登録していない arXiv ID（例: `2005.14165`）を入力 → 候補がプレビュー → Wishlist 登録（R13）。arXiv の PDF を 2 つドラッグ&ドロップ → 両方登録、同じ PDF をもう一度 → 重複表示（R12）。
6. ResNet を開く → PDF 表示、テキストをハイライトしてコメント、図を矩形選択して保存 → リロード後も同じ位置に出る → 注釈一覧のクリックでその位置へ飛ぶ（R15–R18）。
7. ノートに Markdown を書く → 自動保存 → 別 PC でリロードしても残る。`<script>` を書いても実行されない（R18）。
8. 書誌タブでタグとコレクションを付け、BibTeX をコピーして貼り付けると正しい（R11, R18, R20）。
9. 設定で PAT を発行 → 一度だけ表示 → `curl -H "Authorization: Bearer <token>" http://<host>:3080/api/v1/papers/light` が 200 → 失効 → 401（R8）。
10. パスワードを変更 → ログアウト → 新しいパスワードでログインでき、古いパスワードは失敗する（R7）。
11. テスト用論文を 1 件削除 → 一覧から消え、`data/library` の該当 PDF も消える。他ワークスペースと共有中の PDF は残る（R21、共有ケースは統合テストで確認）。
12. viewer 権限のワークスペースに切り替える → 追加・編集・注釈作成の操作ができない（R19。テスト用に共有ワークスペースとメンバーを API で用意する）。

## 決めたこと
- **進める順番**: AI 以外の画面を先に作り、AI は Phase 3 の後に Phase 4b として足す（ユーザー判断）。右ペインのタブは AI タブを後から差し込める前提で作る。
- **追加範囲**: 論文削除、コレクション管理、パスワード変更を入れる。共有ワークスペースのメンバー管理画面は入れない（ユーザー判断）。
- **言語**: 日英切り替え（ユーザー判断）。初期値はブラウザ言語、URL にロケールは含めない（ブックマークや API パスを単純に保つため）。
- **認証の方式**: 既存の Cookie セッション（`bf_session`）をそのまま使う。フロントは Next.js の `/api` リライト経由で同一オリジンとして API を叩くため、トークンを JS に持たせない。CSRF の Origin チェックは `CORS_ALLOWED_ORIGINS` に LAN の URL を入れて通す（ローカル `.env` は設定済み）。
- **PDF の取得**: 既存の `GET /papers/{id}/file` を同一オリジンで読む。ビューアは PDF.js ベースで、ハイライトと矩形選択の座標を `annotations.position`（JSONB、Phase 1 で react-pdf-highlighter 互換と定義済み）に保存する。ライブラリはライセンスが MIT/Apache 系のものから実装時に選ぶ。
- **UI の部品**: AGENTS.md §2 どおり Tailwind + shadcn/ui。追加する依存パッケージ（UI 部品、データ取得、PDF ビューア、Markdown 表示とサニタイズ、i18n）は実装開始時に一覧にして承認を取る。
- **注釈の権限**: 閲覧はワークスペースの viewer 以上、作成は editor 以上。編集・削除は作成者本人かワークスペースの owner。
- **論文削除と共有 PDF**: Phase 2 で同じ内容の PDF を複数ワークスペースで共有しているため、DB 上の参照がなくなったときだけファイルを消す。
- **パスワード変更とセッション**: 変更時、今のセッション以外は失効させたいが、既存の仕組みは jti 単位の denylist しかない。ユーザー単位の「この時刻より前のセッションは無効」を持つかは実装時に決める（DB に列を足す場合はマイグレーション `0003_*`）。
- **ファイル名**: スキルの `specs/NNN-name.md` 形式ではなく、このリポジトリの慣例（`specs/phaseN.md`）に合わせた。
- **確認環境**: ローカルの `.env` は `BIND_ADDRESS=0.0.0.0`、`FRONTEND_PORT=3080`（3000 は別のサービスが使用中）、`ENABLE_DOCS=true`、CORS に LAN の URL を追加済み。テスト用ユーザー `e2e@biblioflow.example.com` に 5 件の論文が入っている。

## 承認時に決めたこと（2026-11-03）
- **npm 依存**: shadcn/ui 一式（`@radix-ui/*` 必要分、`class-variance-authority`, `clsx`, `tailwind-merge`, `tailwindcss-animate`, `lucide-react`, `sonner`）、`@tanstack/react-query`、`react-pdf-highlighter-extended` + `pdfjs-dist`（React 19 で動かなければ `react-pdf-highlighter` か pdfjs-dist 直書き）、`react-markdown` + `remark-gfm` + `rehype-sanitize`、`eslint` + `eslint-config-next`。i18n はライブラリなし（自前辞書。`en` を `ja` の型で縛り未翻訳キーを typecheck で検出）。
- **パスワード変更時のセッション失効**: `users.sessions_valid_after`（timestamptz, NULL 可）を追加（`0003_sessions_valid_after`）。JWT の `iat` がこれより前なら 401。変更したリクエスト自身には新しい Cookie を発行し直す。PAT はパスワード変更では失効させない（スコープ付きの別資格情報のため）。

## 追加 API の契約（frontend と backend の共通認識）
既存: `GET /api/v1/papers/light`（`X-Total-Count` ヘッダあり, `workspace_id` 等で絞り込み）、`PATCH /papers/{id}`（tags / collection_ids / notes_md 等）、`POST /papers/upload`（form: file, workspace_id, paper_id で wishlist に紐付け）、`GET /papers/{id}/file`、`GET /papers/lookup`、`POST /papers/metadata-only`、`/auth/login|logout|me`、`/tokens`、`/workspaces`。
- `POST /api/v1/auth/password` `{current_password, new_password}` → 204。現在のパスワード誤りは 400、ポリシー違反は 422。session 認証のみ（PAT は 403）。レートリミットあり。
- `DELETE /api/v1/papers/{id}` → 204（editor 以上）。注釈・タグ付け・コレクション所属・チャンクは CASCADE。PDF は同じ `relative_file_path` を参照する論文が他になければ削除。
- `GET /api/v1/tags?workspace_id=` → `[{id, name, color, paper_count}]`（viewer 以上、名前順）。
- `GET /api/v1/collections?workspace_id=` → `[{id, workspace_id, name, parent_id, paper_count, created_at, updated_at}]`（viewer 以上）。
- `POST /api/v1/collections` `{workspace_id, name, parent_id?}` → 201（editor）。`PATCH /collections/{id}` `{name?, parent_id?}`（自分や子孫を親にしたら 422、別ワークスペースの親は 404）。`DELETE /collections/{id}` → 204（子コレクションも削除、論文は残る）。
- `GET /api/v1/papers/{paper_id}/annotations` → `[{id, paper_id, user_id, user_display_name, kind, page_number, position, content_text, comment, color, created_at, updated_at}]`（viewer 以上、page_number→created_at 順）。
- `POST /api/v1/papers/{paper_id}/annotations` `{kind: "highlight"|"area"|…既存 enum, page_number, position, content_text?, comment?, color?}` → 201（editor）。position は JSON オブジェクト（64KB 上限）、color は `#rrggbb`。
- `PATCH /api/v1/annotations/{id}` `{comment?, color?, position?}`、`DELETE /api/v1/annotations/{id}` → 204。作成者本人かワークスペース owner のみ（それ以外で論文が見えるなら 403、見えないなら 404）。
- 書き込み系は `write:papers`、読み取りは `read:papers` スコープ。

## 実装中に決めたこと・確認結果（2026-11-03）
- PDF ビューアは `react-pdf-highlighter-extended` 8.1.0（peer は React 18 だが `overrides` で React 19 に寄せて動作確認済み）。pdf.js の worker は `scripts/copy-pdf-worker.mjs`（prebuild/predev）で `public/` にコピーして同一オリジンから配信する（CDN を使わない）。
- ライブラリの `MouseSelection` は `enableAreaSelection` をマウント時に閉じ込めるため、範囲選択モードは ref で読む（`pdf-pane.tsx`）。
- 注釈の編集・削除ボタンは PDF 上でも一覧でも「editor 以上かつ作成者本人 or owner」のときだけ出す（バックエンドも同じ規則。作成者が viewer に降格したら変更不可）。
- `npm run lint` は `next lint`（`.eslintrc.json`）。flat config への移行は追加依存が要るので見送り。
- 受け入れ基準の確認は別 PC の代わりに LAN のアドレスを headless Chromium で開いて行った。テスト用に `qa-owner@` / `qa-viewer@biblioflow.example.com` と共有ワークスペース「QA Lab」（qa-viewer は viewer）をローカル DB に作成。
- R9 の「1,000 件で 1 秒以内」は件数を用意しての計測はしていない（数件で検索〜表示が即時であることのみ確認）。
- パスワード変更時の失効判定は秒単位のため、変更と同じ秒に発行された他セッションは残る（既知の制約）。
