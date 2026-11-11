# エージェントの開発体制（親セッション + 子セッション）

2026-11-07 にユーザーが承認した体制。biblioflow と biblioflow-web をまたぐ作業で、ユーザーがセッション間の中継をしなくて済むようにする。

## 役割
- **親**（biblioflow で動くセッション 1 つ）: 作業を分解し、子に「担当リポジトリ・ファイル・完了条件」を渡す。子の報告を diff とテスト結果で確かめ、リポジトリ間の順序（どの PR を先にマージするか）を決める。`STATUS.md` を書くのは親だけ。ユーザーには判断が要ることだけを上げる。
- **子**（デスクトップアプリの子セッション。担当リポジトリの worktree・作業ブランチで動く）: 担当範囲だけを実装・テストし、ローカルにコミットする。push・PR・マージ・タグ・デプロイはしない。詰まったら親に送る。子どうしの受け渡しも親を通す。
- **subagent**: 子の既定（全自動で親に報告が返る）。調査・レビュー・検証にも使う。数時間かかる仕事や、ユーザーが直接触りたい仕事だけ子セッションにする（Remote Control 経由のターンでは子セッションを起動できない）。

## 受け渡しフォーマット
子は作業ブランチの `.handoff/<task>.md`（gitignore 対象）に書き、親への最後の報告の先頭にも同じ要約を付ける。

```yaml
task: <親が付けた ID>
repo: <biblioflow | biblioflow-web>  branch: <作業ブランチ>
status: done | blocked | needs-decision
did: [やったこと]
files: [変更したファイル]
tests: "<実行したコマンド>: <結果>"   # 省略しない。落ちたら出力を付ける
commits: [<sha>]                     # ローカルのみ、未 push
next: [次に必要なこと（誰が）]
needs_from_others: []                # 他リポジトリ・他の子の成果が要る場合
decision: null                       # 要判断なら 1 問 + 選択肢
```

## 権限
| 自動で可 | ユーザーの承認が必須 |
|---|---|
| ファイル編集、build / lint / typecheck / テスト、開発用の `docker compose` | 承認済みの計画にない変更の push・PR・マージ |
| 作業ブランチ・worktree の作成、ローカルコミット、`git merge origin/main` | タグ・リリース・デプロイ（`deploy.sh`・`wrangler`） |
| `gh` の閲覧（pr view / checks / run view） | 秘密（`.env`・秘密のディレクトリ・`op`）、大量削除、依存の追加 |
| 子（subagent・子セッション）の起動 | 課金がありうる操作（`gh workflow run` など）、外部への送信（X・メール・他人のリポジトリへの PR や issue） |
| **承認済みの計画の項目**: push → PR → CI green → squash マージ（1 項目ずつ） | |

**承認は計画単位**（2026-11-07、ユーザー決定）: ユーザーが spec か `STATUS.md` の計画を承認したら、その項目は親が push からマージまで進める。そのため push・PR 作成・マージは `ask` にせず、運用で守る（force push だけは `deny`）。計画の外に出る変更が必要になったら、その時点で止めて聞く。例外: **biblioflow-web の main へのマージは本番サイトへの公開**（Cloudflare が main から自動でデプロイする）なので、計画の項目でも毎回承認を取る。

仕組み: `.claude/settings.json` の `ask` / `deny` と、PreToolUse フック `.claude/hooks/guard.py`（`git -C . push` や `sh -c` のような、ルールが前方一致で見落とす書き方を拾う）。Bash のルールもフックも安全の境界ではない（Claude Code のドキュメントのとおり）。最終的な歯止めは main のルールセット・GitHub の権限・ここに書いた運用。

## 報告
親はターンの終わりに「進捗 / 要判断 / 詰まり」の 3 行と判断 1 問だけを出す。`STATUS.md` は節目（子の完了・判断待ち・PR のマージ）で更新する。公開リポジトリなので、ローカル固有の情報は書かない。

## ループ（2026-11-12、ユーザーが権限を委譲）
`/loop /dev-loop` で、キュー `LOOP.md` の項目を 1 周 1 項目ずつ処理する。手順は `.claude/skills/dev-loop/SKILL.md`。

- **今の開発に影響しない**: ループの作業はすべて `.claude/worktrees/loop-<id>`（ブランチ `loop/<id>`）で行い、ユーザーのチェックアウトには触らない。backend のテストは `scripts/loop-test.sh`（イメージ `biblioflow-loop-api:dev`、DB `biblioflow_loop_test`、Valkey の DB 14）で、開発環境のイメージ・テスト DB（`biblioflow_test`・DB 15）・稼働中のコンテナを置き換えない。開発環境の起動・停止もしない。
- **承認の単位は今までどおり**: ループが自分で進めるのは `ready`（計画が承認済み）の項目だけ。`spec` の項目は spec の PR を出して待つ。上の表の「ユーザーの承認が必須」は、ループの中でも毎回聞く（その項目を `waiting` にして止まり、通知する）。
- **1 周の上限**: 1 項目・PR 1 つ。CI の修正は 3 回まで。何もすることがなければループを終える（空回りしない）。
- **記録**: `LOOP.md` と `STATUS.md` は PR の中で更新する（親だけが書く、の「親」にループも含む）。周ごとのログは `.handoff/loop-log.md`（ローカル）。
- ループを回すセッションは 1 つだけ。ユーザーが同じリポジトリで別の作業をしていてもよい（ファイルが重なりそうな項目は `waiting` にして聞く）。
