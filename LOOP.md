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
| security-baseline | セキュリティの基盤（SECURITY.md、CodeQL、イメージの脆弱性検査、audit.yml の extension ジョブ、Actions の SHA 固定と zizmor、RELEASING のセキュリティの節、任意の更新通知） | waiting | spec 承認済み（`specs/security-baseline.md`、2026-11-12。private vulnerability reporting の有効化も承認）。親セッションが実装中なので、ループは触らない |
| community | コミュニティのファイル（CONTRIBUTING・CODE_OF_CONDUCT・issue / PR テンプレート）と README の刷新 | waiting | spec 承認済み（`specs/community.md`、2026-11-12）。親セッションが実装するので、ループは触らない |
| d-csl | トラック D: 引用スタイル（CSL）での参考文献の出力のみ | waiting | spec 承認済み（`specs/csl-bibliography.md`、2026-11-12）。親セッションが実装するので、ループは触らない |
| translate | PDF の翻訳: 選んだ文章の翻訳のみ | waiting | spec 承認済み（`specs/translate-selection.md`、2026-11-12）。親セッションが実装するので、ループは触らない |
| dep-pypdf7 | pypdf 6 → 7（Dependabot #61）: 変更点の調査、PDF の検査・チャンク抽出（`pdf.py`・`chunking.py` の `visitor_text`、1 文字ずつの行の処理）への影響、移行の計画 | spec | #61 は CI が通っている。メジャー更新なのでマージは spec の承認後 |
| dep-bibtexparser2 | bibtexparser 1.x → 2（Dependabot #62）: 2 系の API への移行（取り込みのパーサ、`@string` の保持、子プロセスでの解析）の計画 | spec | #62 は compose のジョブが落ちる。AGENTS.md 6.2 の「`bibtexparser` 1.x」の記述も更新が要る |

## ユーザー待ち（ループの対象外）
- v0.3.0 のタグ・Release・本番の更新（タグ前のチェックは通った。インストーラのメッセージの修正 #63 をマージしてから）
- v1 の基準 1: Zotero をインストールして実際の書き出しで確かめる（承認済み 2026-11-12。親セッションが行う。Mendeley は v1 の条件から外した）
- Dependabot のメジャー更新: v1 の基準を終えてから 1 つずつ spec を書く（ユーザー判断 2026-11-12、#55）。新しく来た pypdf 7（#61）・bibtexparser 2（#62）の扱いはユーザーに聞く
- 開発環境の `EMBEDDING_PROVIDER`
