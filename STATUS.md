# STATUS

親セッションだけが更新する（体制は `specs/agent-workflow.md`）。最終更新: 2026-11-07

## 進行中
| タスク | リポジトリ / ブランチ | 担当 | 状態 |
|---|---|---|---|
| file-sha256: chunks・詳細 API と MCP で PDF の SHA-256 を返す | biblioflow / `feat/file-sha256` | 子 A | PR・CI 待ち |
| phase3d-spec: 3d（チャット・Vision 解説）の spec | biblioflow / `docs/phase3d-spec` | 子 B | draft PR・ユーザーの判断待ち |

## 承認待ち
- 3d の spec の「決めてほしいこと」6 問

## 予定
- biblioflow-mcp を外部の MCP カタログ（HOL Guard）に登録する: v1.0 になってから（PyPI に出すのが前提。それまでは見送りと回答する）

## 詰まり
- なし（Remote Control 経由では子セッションを起動できないので、子は subagent を既定にした）

## 直近の完了
- 開発体制の試行 1 回目（biblioflow #23・biblioflow-web #4）。ユーザーの中継 0 回
- v0.2.0 リリース（#21）
