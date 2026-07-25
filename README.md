# ArchitectAI

漠然としたシステムのアイデアを、AIとの対話だけで「要件定義 → 構成案 →
レビュー → 初期インフラコード」まで一気通貫で形にするための、
個人開発のシステム設計アシスタントです。

ローカルLLM（Ollama）による無料のヒアリング・構成案作成と、
Claude Webによる最終レビュー（Human-in-the-Loop）を組み合わせることで、
API課金ゼロかつ人間の最終判断を必ず介在させる設計にしています。

---

## プロジェクトの目的

新規システムの立ち上げ時、「何を作りたいかは何となくあるが、要件として
言語化し、技術構成に落とし込む」作業には時間がかかります。ArchitectAIは
このプロセスを、

1. AIとの対話で要件を掘り下げる（ヒアリング）
2. 対話内容から構成案をAIに作成させる
3. 人間（Claude Web）が構成案をレビューし、最終JSONとして確定する
4. 確定したJSONから、初期インフラコード一式（`docker-compose.yml`等）を
   自動生成する

という4段階に分解し、各段階を専用の状態機械（`HEARING` →
`PROPOSED` → `CLAUDE_REVIEW` → `COMPLETED`）として管理することで、
「思いつきレベルのアイデア」から「動かせる初期構成」までの距離を
縮めることを目的としています。

---

## 画面一覧

### 初期画面

![初期画面](docs/screenshots/readme-initial.png)

プロジェクト名を入力するだけで、ヒアリング用のセッションが自動生成
されます。左パネルには過去に作成したプロジェクトの一覧が表示され、
`session_id`をlocalStorageに永続化しているため、画面をリロードしても
作業中の状態（ヒアリング途中・レビュー中・確定済みのいずれでも）が
失われません。

### ヒアリング中

![ヒアリング中の画面](docs/screenshots/readme-hearing.png)

ヘッダー右上のパイプライン表示（ヒアリング → 構成案 → レビュー →
確定）で、現在このプロジェクトがどの段階にあるかが常に分かるように
しています。プロジェクト一覧からは、いつでも別プロジェクトへ
ワンクリックで切り替えられます。

### 確定済み（JSON編集・エクスポート）

![確定済み画面](docs/screenshots/readme-completed.png)

Claude Webでのレビューを経て確定した構成JSONは、画面上でそのまま
再編集できます。編集後は既存のバリデーション・サニタイズ処理を
再度通した上で新しいバージョンとして保存され、いつでも最新の内容で
ZIPをダウンロードできます。

---

## システムの流れ

```
[HEARING]                [PROPOSED]              [CLAUDE_REVIEW]           [COMPLETED]
ユーザーが要件を   --->  ローカルLLMが構成案 --->  Claude Webへ手動   --->  確定JSONから
チャットで伝える          を作成（Ollama）          貼り付けでレビュー       IaC一式をZIP生成
                                                    ・確定JSONを提出
```

1. **ヒアリング（`HEARING`）**：ユーザーがチャットで要件を伝えると、
   ローカルのOllama（`gemma4:e4b-it-q4_K_M`）がPMとして深掘りの質問を
   返します。API課金は発生しません。
2. **構成案作成（→`PROPOSED`）**：「構成案を作成してもらう」を押すと、
   それまでの会話内容をもとにOllamaが構成案を作成します。
3. **Claudeへのハンドオフ（→`CLAUDE_REVIEW`）**：構成案からレビュー用
   プロンプトを自動生成し、クリップボードにコピー。ユーザーがClaude
   Webへ手動で貼り付けてレビューを依頼します（**意図的な
   Human-in-the-Loop**。詳細は下記「技術的な工夫」参照）。
4. **確定（→`COMPLETED`）**：Claude Webの出力（JSON）を画面に貼り付けて
   送信すると、フェンス除去・JSONパース・スキーマバリデーションを経て
   構成が確定します。以降、画面上でJSONを直接編集して再生成することも
   できます。
5. **IaC生成**：確定済みJSONをJinja2テンプレートに流し込み、
   `docker-compose.yml`と`NOTES.md`を含むZIPをメモリ上で生成します。

---

## 技術構成

| レイヤー | 技術 |
|---|---|
| フロントエンド | Next.js 15 (App Router) / React 19 / TypeScript / Tailwind CSS |
| バックエンド | FastAPI / SQLAlchemy 2.0 (同期) / Pydantic v2 |
| データベース | PostgreSQL 16 (Docker) |
| ヒアリング・構成案AI | ローカルLLM: Ollama (`gemma4:e4b-it-q4_K_M`) |
| 最終レビューAI | Claude Web（Human-in-the-Loop、貼り付け方式） |
| IaC生成 | Jinja2テンプレート → メモリ上でZIP化 |
| インフラ | Docker Compose（`db` / `backend` / `frontend`の3コンテナ） |

役割ベースで整理すると、**日常的なヒアリング・構成案作成はローカルAIに
任せてコストゼロで回し、最終的な品質チェックだけを人間＋Claudeに
委ねる**という構成です。

---

## 技術的な工夫

- **Human-in-the-Loopを意図的に維持する設計**：AIが生成した構成案を
  そのままシステムに反映せず、必ずClaude Webでの人間によるレビューを
  経由させています。一方で、レビュー後の確定JSONは画面上で直接編集・
  再生成できるようにしており（`PUT /edit_claude_json`）、
  「レビューの厳格さ」と「確定後の修正しやすさ」を両立させています。
  この編集機能はレビューをバイパスする設計判断であることを明記した
  上で、既存のバリデーション・サニタイズ処理は必ず通す実装にしています。
- **YAMLインジェクション対策**：Claude Webの出力やユーザーによる直接
  編集からサービス名（`services[].name`）を受け取る際、英数字・
  ハイフン・アンダースコアのみを許可する正規表現バリデーションを
  Pydanticの`field_validator`で強制しています。生成される
  `docker-compose.yml`にコロンや改行を含む値がそのまま埋め込まれ、
  意図しないYAML構造の破壊やキーの追加が起きることを防いでいます。
- **確定JSONの編集履歴を保持**：確定・再編集のたびに新しい
  `role=system`メッセージとして`messages`テーブルに追記し、古い履歴を
  削除しません。`/export`は常に最新のメッセージを参照するため、
  過去の確定内容を失うことなく「常に最新の構成をエクスポートできる」
  状態を両立しています。
- **状態遷移の一元管理**：`HEARING`→`PROPOSED`→`CLAUDE_REVIEW`→
  `COMPLETED`という許可された遷移のみをコード上の1箇所
  （`ALLOWED_STATE_TRANSITIONS`）に列挙し、それ以外の遷移は
  サービス層で拒否します。UI側のパイプライン表示もこの状態を
  唯一の情報源としています。
- **セッション状態のlocalStorage永続化**：`project_id`/`session_id`を
  localStorageに保存し、画面リロード時にはプロジェクト一覧と突き合わせて
  復元します（既存プロジェクトの一覧取得APIを使い、404相当のケースは
  一覧に見つからないことで判定）。ヒアリング途中・レビュー中・確定済み
  いずれの状態でも作業内容を失いません。
- **環境変数化によるセキュリティ対応**：当初`docker-compose.yml`に
  平文で記載していたPostgreSQLの認証情報を`.env`ファイルへ分離し、
  `${POSTGRES_USER}`等の変数参照に置き換えました。`.env`は
  `.gitignore`で除外し、代わりに値をプレースホルダー化した
  `.env.example`をリポジトリに含めています。

---

## セットアップ手順

### 前提条件

- Docker Desktop（Windows / Mac）がインストール・起動済みであること
- Ollamaがホストマシン上で起動しており、`gemma4:e4b-it-q4_K_M`モデルが
  取得済みであること

```powershell
ollama pull gemma4:e4b-it-q4_K_M
ollama serve
```

### 環境変数の設定

`.env.example`をコピーして`.env`を作成し、必要に応じて値を変更して
ください（ローカル検証用途であれば、そのままでも動作します）。

```powershell
cd architect-ai
copy .env.example .env
```

`.env`には以下の3項目を設定します。

```
POSTGRES_USER=architect
POSTGRES_PASSWORD=architect
POSTGRES_DB=architectai
```

### 起動手順

```powershell
docker compose up --build
```

- Frontend: http://localhost:4500
- Backend API: http://localhost:8000/docs

### 停止

```powershell
docker compose down
```

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| チャットが応答なしでハングする | Ollamaが起動していない / モデル未取得 | `ollama list`でモデル存在を確認。バックエンドは接続失敗時に明示的なエラーメッセージを返す設計です |
| backendがdbに接続できない | dbの起動待ちが不足 / `.env`の値が不一致 | `depends_on.condition: service_healthy`済みだが、初回起動時は数分かかる場合あり。`.env`の値を変更した場合、既存のDBボリュームとの認証不整合に注意してください |

---

## 今後の拡張

- 1プロジェクトにつき複数セッションを保持できるようにし、同じ
  プロジェクトで要件定義をやり直せるようにする
- IaC生成テンプレートの対象をDocker Compose以外（Terraform等）にも
  拡張する
- Claude Webとの手動貼り付けに代えて、Claude APIとの直接連携を
  選択できるオプションを追加する（API課金と引き換えに、完全自動化を
  選べるようにする）
