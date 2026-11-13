# LOOP

開発ループ（`.claude/skills/dev-loop/SKILL.md`、`/loop /dev-loop` で回す）のキュー。上から順に処理する。体制は `specs/agent-workflow.md`。

状態:
- `ready` — ユーザーが計画を承認済み。ループが実装 → PR → CI green → マージまで進める
- `spec` — 計画がまだない。ループは spec を書いて PR を出し、承認を待つ（マージしない）
- `waiting` — ユーザーの判断・操作待ち（理由を書く）。ループは飛ばす
- `done` — 完了（PR 番号）

項目を足す・順番を変える・`spec` を `ready` にするのはユーザー（または、ユーザーの承認を受けた親セッション）。ループ自身は `ready` を作らない。

## キュー
| ID | 内容 | 状態 | メモ |
|---|---|---|---|
| security-baseline | セキュリティの基盤（SECURITY.md、CodeQL、イメージの脆弱性検査、audit.yml の extension ジョブ、Actions の SHA 固定と zizmor、RELEASING のセキュリティの節、任意の更新通知） | done | #65・#66・#68・#69・#71 |
| community | コミュニティのファイル（CONTRIBUTING・CODE_OF_CONDUCT・issue / PR テンプレート）と README の刷新 | done | #72（Code of Conduct はユーザーの連絡先待ち） |
| d-csl | トラック D: 引用スタイル（CSL）での参考文献の出力のみ | done | #74 |
| translate | PDF の翻訳: 選んだ文章の翻訳のみ | done | #76 |
| v1-compat | v1 の基準 3（互換性の約束）: リリースごとに OpenAPI（`openapi.json`）を Release に添えて公開し、API の破壊的変更を検出する（前のタグとの差分）。CI で前のリリースタグを入れてから今の版にアップグレードし、`restore.sh --check` まで通す | spec | `RELEASING.md` の基準 3。新しいワークフローや Release への添付はユーザーの承認が要る（spec に書く） |
| dep-pypdf7 | pypdf 6 → 7（Dependabot #61）: 変更点の調査、PDF の検査・チャンク抽出（`pdf.py`・`chunking.py` の `visitor_text`、1 文字ずつの行の処理）への影響、移行の計画 | spec | #61 は CI が通っている。メジャー更新なのでマージは spec の承認後 |
| dep-bibtexparser2 | bibtexparser 1.x → 2（Dependabot #62）: 2 系の API への移行（取り込みのパーサ、`@string` の保持、子プロセスでの解析）の計画 | spec | #62 は compose のジョブが落ちる。AGENTS.md 6.2 の「`bibtexparser` 1.x」の記述も更新が要る |
| dist-clients | v1 の基準 6（クライアントの配布）の準備: biblioflow-mcp を PyPI に出せる形にする（パッケージの中身・版・trusted publishing の手順）と、拡張のストア掲載の文面・スクリーンショット・権限の説明。公開そのものはしない | spec | 公開・新しいワークフロー・アカウントの作成は毎回ユーザーの承認 |

## ユーザー待ち（ループの対象外）
- v0.3.0 のタグ（1a46a61）・GitHub Release・本番の更新・本番のバックアップの cron（タグ前のチェックは 2026-11-12 に通った）
- CODE_OF_CONDUCT.md の連絡先（専用のアドレス）。届いたら小さな PR で足し（Contributor Covenant 2.1 を参照する短い文面と、そのアドレスで受ける「Reporting」の節）、CONTRIBUTING からリンクする
- GitHub Sponsors の tier（文案は `specs/community.md` の末尾）
- Dependabot: setup-uv 7 → 10（#81）は Actions のメジャー更新なのでユーザーの承認が要る。codeql-action 4.39.3（#78〜#80）は minor なので通常どおり CI green でマージしてよい
- Dependabot のメジャー更新（TypeScript 7、tailwind-merge 3、Tailwind 4、mcp SDK 2.x）: v1 の基準を終えてから 1 つずつ spec（ユーザー判断 2026-11-12）
- v1 の基準 5（本番での常用 4 週間・実際の復元・外の利用者 3 人）はユーザーの運用
- 開発環境の `EMBEDDING_PROVIDER`
