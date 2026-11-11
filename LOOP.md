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
| community | コミュニティのファイル（CONTRIBUTING・CODE_OF_CONDUCT・issue / PR テンプレート）と README の刷新 | spec | GitHub Sponsors の設定・リポジトリの説明文の変更は外部の設定なのでユーザーが行う |
| d-csl | トラック D: 引用スタイル（CSL）での参考文献の出力のみ | spec | `specs/roadmap.md` |
| translate | PDF の翻訳: 選んだ文章の翻訳のみ | spec | |

## ユーザー待ち（ループの対象外）
- v0.3.0 のタグ・Release・本番の更新（タグ前のチェックは通った。インストーラのメッセージの修正 #63 をマージしてから）
- v1 の基準 1: Zotero をインストールして実際の書き出しで確かめる（承認済み 2026-11-12。親セッションが行う。Mendeley は v1 の条件から外した）
- Dependabot のメジャー更新: v1 の基準を終えてから 1 つずつ spec を書く（ユーザー判断 2026-11-12、#55）。新しく来た pypdf 7（#61）・bibtexparser 2（#62）の扱いはユーザーに聞く
- 開発環境の `EMBEDDING_PROVIDER`
