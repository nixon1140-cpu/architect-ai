# ArchitectAI 仕様メモ

コードと README から分かる範囲をまとめたものです。元の仕様書はこのリポジトリにはありません。

## 目的
ぼんやりしたシステムのアイデアを、AIとの対話で要件定義 → 構成案 → レビュー → 初期インフラコードにする。

## 状態の流れ
`HEARING` → `PROPOSED` → `CLAUDE_REVIEW` → `COMPLETED`（許可された移動は `models.py` の `ALLOWED_STATE_TRANSITIONS` のみ）

## API（すべて `/api/v1`、ログイン以外は JWT 必須）
| メソッド | パス | 役割 |
|---|---|---|
| POST | /auth/register | 新規登録（トークンは出さない） |
| POST | /auth/login | ログイン（24時間有効のJWTを発行） |
| POST | /projects | プロジェクト作成（セッションも自動作成） |
| GET | /projects | 自分のプロジェクト一覧 |
| DELETE | /projects/{id} | プロジェクト削除（sessions・messagesも削除） |
| POST | /sessions/{id}/chat | ヒアリング。`request_proposal=true` で次の状態へ |
| POST | /sessions/{id}/submit_claude_json | Claudeの出力JSONを検査して確定 |
| PUT | /sessions/{id}/edit_claude_json | 確定後のJSONを直接編集（レビューを意図的に省略） |
| GET | /sessions/{id}/export | ZIP出力（`output_format=docker-compose` か `terraform`） |

## データ
`users` / `projects`（user_id）/ `sessions`（current_state）/ `messages`（role: user・assistant・system）。確定JSONとClaude用プロンプトは `role=system` のメッセージとして保存する。

## 確定JSONの形
`project_name`、`services[]`（`name`・`type`・`image_or_build`・`env`・`ports`）、`notes`。`services[].name` は英数字・`-`・`_` のみ。

## 未実装（意図的）
パスワードリセット、メール確認、新規登録の制限。外部公開するなら招待制・レート制限が必要。
