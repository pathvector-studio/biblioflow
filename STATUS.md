# STATUS

親セッションだけが更新する（体制は `specs/agent-workflow.md`）。最終更新: 2026-11-07

## 進行中
| タスク | リポジトリ / ブランチ | 担当 | 状態 |
|---|---|---|---|
| agent-workflow: 体制・権限・STATUS.md | biblioflow / `chore/agent-workflow` | 親 | ローカルコミット済み・承認待ち |
| web-release-docs: フッターに Changelog リンク | biblioflow-web / `docs/release-links` | 子（subagent） | 完了・build green・承認待ち |

## 承認待ち
- 上の 2 本の push・PR 作成・CI green 後のマージ（biblioflow 側は CI・開発体制だけの変更なので `skip-changelog` ラベル）

## 予定
- MCP `read_pages` とチャンク API で `file_sha256` を返す（引用を PDF のバージョンに結び付けるため。外部からの提案を受けて次のリリースで入れると回答する）
- biblioflow-mcp を外部の MCP カタログ（HOL Guard）に登録する: v1.0 になってから（PyPI に出すのが前提。それまでは見送りと回答する）

## 詰まり
- Remote Control 経由のターンでは子セッションを起動できない。そのときは subagent で代行する

## 直近の完了
- v0.2.0 リリース（#21）
