# CLAUDE.md

## このプロジェクトは何か

ArchitectAI は、ぼんやりしたシステムのアイデアを、AIとの会話で「動かせる初期構成ファイル」にするツールです。
作業（セッション）は4つの状態を順に進みます: `HEARING`（質問に答える）→ `PROPOSED`（構成案ができた）→ `CLAUDE_REVIEW`（Claude Webで人がチェック）→ `COMPLETED`（確定）。

- ヒアリングと構成案は、自分のPCのAI（Ollama、`gemma4:e4b-it-q4_K_M`）が担当します。
- チェックは、人がプロンプトをClaude Webに手で貼り、返ってきたJSONを手で貼り戻します。これは**わざとの設計**（Human-in-the-Loop＝最後に人が確認する仕組み）です。頼まれない限りAPI自動連携にしないこと。
- 確定JSONから、Jinja2（ひな形に値を入れる道具）で Docker Compose か Terraform のZIPを作ります。
- 技術: FastAPI・同期版SQLAlchemy・PostgreSQL 16、Next.js 15、JWT＋bcrypt、Alembic（DBの変更履歴管理）。
- 作業の最初に `docs/progress.md`（前回までのメモ）を読むこと。

## よく使うコマンド

```powershell
copy .env.example .env          # JWT_SECRET_KEY は自分で作った値に変える
docker compose up --build       # フロント :4500 / バックエンド :8000 (/docs) / DB :5432
docker exec architectai-backend alembic upgrade head   # 起動時に自動では動かない。手動で実行
docker compose build frontend   # フロントを変えたら毎回必要（本番ビルドで、コードをマウントしていない）
cd backend; pytest              # テスト（メモリ上のSQLite使用）。1つだけなら pytest tests/test_api.py::テスト名
```

フロントのテストはなく、確認は `frontend/` での `npm run lint` / `npm run build` だけです。Ollama はPC側で `ollama serve` を起動しておき、バックエンドは `host.docker.internal:11434` で話しかけます。

## 全体のつくり

**バックエンド（`backend/app/`）**
- `main.py` が中心です（DB接続、ログイン確認 `get_current_user`、エラー処理、全API `/api/v1`）。APIは `async` で、DB処理は同期関数（`_*_sync`）を `run_in_threadpool` で呼びます。
- `core/exceptions.py` の自作エラーは `main.py` でHTTP番号に変換します（Ollama不通502、モデルなし404、Claude JSON不正422、状態遷移ミス・メール重複409、ログイン失敗401）。何でも500にまとめないこと。
- 状態の移動ルールは `models.py` の `is_valid_transition` の1か所だけが正解です。
- 検索は必ず `Project.user_id` で絞り、他人のものは403でなく404を返します（存在を推測させないため）。
- 関連データの削除は、DBの `ondelete` ではなく SQLAlchemy の `cascade="all, delete-orphan"` で行います。
- 確定JSONとClaude用プロンプトは専用の列がなく、`messages` の `role=system` の行です。最新の行が有効で、`edit_claude_json` は古い行を消さず追加します。
- `submit_claude_json` と `edit_claude_json` は同じ検査（フェンス除去→`json.loads`→`ClaudeJSONSubmission`）を通します。`schemas.py` の検査（`services[].name` は英数字と `-` `_` のみ）がYAMLインジェクション対策なので、編集の道でも必ず通すこと。
- `services/ai_agents.py` は httpx でOllamaを直接呼びます（待ち時間上限240秒）。`services/iac_builder.py` は `templates/*.j2` を使い、自由入力は `_yaml_quote` / `_hcl_quote` で安全にしてから渡します。

**DBの変え方**: Alembic だけを使います。起動時の `create_all()` はマイグレーションが空振りする原因になったため外しました（例外は `tests/conftest.py`）。列やテーブルを足すときは `backend/alembic/versions/` に追加します。

**フロント（`frontend/src/`）**: `app/page.tsx` がメイン画面、`lib/api.ts` が通信です。`project_id` と `session_id` はlocalStorageに保存し、開き直したときに一覧と照らして復元します。接続先は `NEXT_PUBLIC_API_BASE_URL` です。

CORS（別サイトからのアクセス許可）は `http://localhost:4500` のみです（`main.py`）。Cloudflare Tunnel など公開アドレスは未対応で、外部公開するなら追加が必要です。

## リポジトリのルール

- `docker compose down -v` は使わない（DBのデータが消えるため）。
- `.claude/` と `.env` は git に入れない（過去に鍵が漏れた事故があったため）。
