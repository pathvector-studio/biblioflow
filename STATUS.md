# STATUS

親セッションだけが更新する（体制は `specs/agent-workflow.md`）。最終更新: 2026-11-07

## 進行中
- なし

## 承認待ち
- なし

## 予定
- MCP `read_pages` とチャンク API で `file_sha256` を返す（引用を PDF のバージョンに結び付けるため。外部からの提案を受けて次のリリースで入れると回答する）
- biblioflow-mcp を外部の MCP カタログ（HOL Guard）に登録する: v1.0 になってから（PyPI に出すのが前提。それまでは見送りと回答する）

## 詰まり
- なし（Remote Control 経由では子セッションを起動できないので、子は subagent を既定にした）

## 直近の完了
- 開発体制の試行 1 回目（biblioflow #23・biblioflow-web #4）。ユーザーの中継 0 回
- v0.2.0 リリース（#21）
