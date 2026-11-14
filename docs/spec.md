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
パスワードリセット、メール確認、新規登録の制限。常時の外部公開はしないため、招待制・レート制限は当面不要。常時公開に変える場合は必要。

## 公開と起動の方法（2026-11-15決定）
- 公開：ふだんは Tailscale Funnel（インターネット公開の機能）を止めておく。実物を見せるときだけ手で公開し（443 → 4500、URL は `https://architect-ai.taild15be6.ts.net/` のみを使う）、見せ終わったら止める。
- スマホなど他の端末からの閲覧（Tailscale Serve）は行わない。
- いつでも見られる見せ方は、README の画面画像で行う。
- 起動：今は IT News Navigator（別リポジトリ）の start-app.bat から一緒に起動されている。これを単独で起動する形にする（作業は別の Issue で行う）。
- CORS：実物を見せる前に、CORS の許可先に `https://architect-ai.taild15be6.ts.net` を追加する（作業は別の Issue で行う）。
