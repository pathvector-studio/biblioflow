# エージェントの開発体制（親セッション + 子セッション）

2026-11-07 にユーザーが承認した体制。biblioflow と biblioflow-web をまたぐ作業で、ユーザーがセッション間の中継をしなくて済むようにする。

## 役割
- **親**（biblioflow で動くセッション 1 つ）: 作業を分解し、子に「担当リポジトリ・ファイル・完了条件」を渡す。子の報告を diff とテスト結果で確かめ、リポジトリ間の順序（どの PR を先にマージするか）を決める。`STATUS.md` を書くのは親だけ。ユーザーには判断が要ることだけを上げる。
- **子**（デスクトップアプリの子セッション。担当リポジトリの worktree・作業ブランチで動く）: 担当範囲だけを実装・テストし、ローカルにコミットする。push・PR・マージ・タグ・デプロイはしない。詰まったら親に送る。子どうしの受け渡しも親を通す。
- **subagent**: 調査・レビュー・検証のように、そのターンで結果が返る仕事に使う。

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
| ファイル編集、build / lint / typecheck / テスト、開発用の `docker compose` | push、PR の作成 |
| 作業ブランチ・worktree の作成、ローカルコミット、`git merge origin/main` | main へのマージ、ブランチの削除 |
| `gh` の閲覧（pr view / checks / run view） | タグ・リリース・デプロイ（`deploy.sh`・`wrangler`） |
| 子セッション・subagent の起動 | 秘密（`.env`・秘密のディレクトリ・`op`）、大量削除、依存の追加、課金がありうる操作（`gh workflow run` など）、メッセージの送信 |

仕組み: `.claude/settings.json` の `ask` / `deny` と、PreToolUse フック `.claude/hooks/guard.py`（`git -C . push` や `sh -c` のような、ルールが前方一致で見落とす書き方を拾う）。Bash のルールもフックも安全の境界ではない（Claude Code のドキュメントのとおり）。最終的な歯止めは main のルールセット・GitHub の権限・ここに書いた運用。親は「push + PR + マージ」を 1 つの判断にまとめて上げる。

## 報告
親はターンの終わりに「進捗 / 要判断 / 詰まり」の 3 行と判断 1 問だけを出す。`STATUS.md` は節目（子の完了・判断待ち・PR のマージ）で更新する。公開リポジトリなので、ローカル固有の情報は書かない。
