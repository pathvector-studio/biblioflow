# STATUS

親セッションだけが更新する（体制は `specs/agent-workflow.md`）。最終更新: 2026-11-07

## 進行中
| タスク | リポジトリ / ブランチ | 担当 | 状態 |
|---|---|---|---|
| 3d（チャット・Vision 解説）: `specs/phase3d.md` の実装計画 1〜6 を 1 PR ずつ | biblioflow | 親 + 子 | 1（#27）〜5（#31）はマージ済み。6（仕上げ: README・AGENTS.md・spec）: PR・CI 待ち。残りは実プロバイダー（Claude・Ollama）の手動確認（キーと外部送信のため要承認） |

## 承認待ち
- なし

## 予定
- biblioflow-mcp を外部の MCP カタログ（HOL Guard）に登録する: v1.0 になってから（PyPI に出すのが前提。それまでは見送りと回答する）

## 詰まり
- なし（Remote Control 経由では子セッションを起動できないので、子は subagent を既定にした）

## 直近の完了
- 3d の 5: 矩形選択の Vision 解説、Pillow 追加（#31）
- 3d の 4: 横断チャット（#30）
- 3d の 3: 単一論文のチャット（#29）
- 3d の 2: 会話の保存（#28）
- 3d の 1: LLM 層と設定（#27）
- 3d-1: LLM 層と設定（#27）
- 3d の spec（#26）
- PDF の SHA-256 を chunks・詳細 API と MCP で返す（#25）
- 開発体制の試行 1 回目（biblioflow #23・biblioflow-web #4）。ユーザーの中継 0 回
- v0.2.0 リリース（#21）
