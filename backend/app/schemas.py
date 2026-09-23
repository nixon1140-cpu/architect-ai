"""Pydantic v2 スキーマ定義。

- チャット（ヒアリング）エンドポイントの入出力
- Claude Web由来JSONの受信・検証（```json フェンス除去 + 型/値バリデーション）
仕様書 6章・10章 参照。
"""

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

# サービス名はYAML特殊文字混入を防ぐため英数字・ハイフン・アンダースコアのみ許可する。
_SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

# bcryptアルゴリズム自体の仕様上の制約（core/security.py参照）。これを超える
# パスワードをbcryptにそのまま渡すとValueErrorで落ちるため、リクエスト受信時点
# （register/loginとも）で明示的に弾く。
_BCRYPT_MAX_PASSWORD_BYTES = 72


def _validate_password_byte_length(value: str) -> str:
    byte_length = len(value.encode("utf-8"))
    if byte_length > _BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(
            f"パスワードが長すぎます（{byte_length}バイト）。"
            f"{_BCRYPT_MAX_PASSWORD_BYTES}バイト以内にしてください"
            "（日本語等のマルチバイト文字は1文字が複数バイトになる点に注意してください）。"
        )
    return value

# ```json ... ``` / ``` ... ``` のようなMarkdownコードフェンスを除去するための正規表現。
_JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def strip_json_fence(raw_text: str) -> str:
    """Claude Webからの貼り付けテキストに含まれるMarkdownのコードフェンスを除去する。

    フェンスが無い場合はそのまま返す。
    """
    return _JSON_FENCE_PATTERN.sub("", raw_text).strip()


class ChatMessageRequest(BaseModel):
    """要件定義ヒアリングエンドポイントへのユーザー入力。"""

    message: str = Field(..., min_length=1, description="ユーザーからのチャット入力")


class ChatMessageResponse(BaseModel):
    """ヒアリング・提案エンドポイントの応答。"""

    reply: str = Field(..., description="Ollama(gemma4)による応答本文")
    current_state: str = Field(..., description="このメッセージ処理後のセッション状態")
    claude_handoff_prompt: Optional[str] = Field(
        default=None,
        description=(
            "current_stateがCLAUDE_REVIEWに遷移した際、"
            "ユーザーがClaude Webに貼り付けるための提出用プロンプト"
        ),
    )


class ServiceDefinition(BaseModel):
    """Claude Web出力JSONの services[] 要素。"""

    name: str = Field(..., description="サービス名（英数字・ハイフン・アンダースコアのみ）")
    type: str = Field(..., description="例: frontend/backend/database")
    image_or_build: str = Field(..., description="Dockerイメージ名、またはビルドコンテキストパス")
    env: dict[str, str] = Field(default_factory=dict)
    ports: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SERVICE_NAME_PATTERN.fullmatch(value):
            raise ValueError(
                f"services[].name の値 '{value}' が不正です。"
                "英数字・ハイフン(-)・アンダースコア(_)のみ使用できます。"
            )
        return value


class ClaudeJSONSubmission(BaseModel):
    """Claude Webレビュー後の最終確定JSON（フェンス除去・パース後の構造）。"""

    project_name: str = Field(..., min_length=1)
    services: list[ServiceDefinition] = Field(..., min_length=1)
    notes: Optional[str] = None


class ClaudeRawSubmission(BaseModel):
    """フロントエンドから送られてくる、Claude Web出力の貼り付けテキストそのもの。

    ```json フェンスを含んでいてもよい（バックエンド側で除去する）。
    """

    raw_text: str = Field(..., min_length=1, description="Claude Webの出力をそのまま貼り付けたテキスト")


class ClaudeSubmissionResponse(BaseModel):
    """submit_claude_json エンドポイントの成功応答。"""

    status: str = Field(default="ok")
    current_state: str


class UserRegisterRequest(BaseModel):
    """新規登録エンドポイント（POST /api/v1/auth/register）への入力。"""

    email: EmailStr = Field(..., description="ログインに使用するメールアドレス")
    password: str = Field(..., min_length=8, description="パスワード（8文字以上、72バイト以内）")

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value: str) -> str:
        return _validate_password_byte_length(value)


class UserRegisterResponse(BaseModel):
    """新規登録エンドポイントの応答。登録のみ行い、ログインは別途行う想定のため
    トークンは発行しない。"""

    user_id: str = Field(..., description="作成されたユーザーのID")
    email: str


class UserLoginRequest(BaseModel):
    """ログインエンドポイント（POST /api/v1/auth/login）への入力。"""

    email: EmailStr = Field(..., description="登録済みのメールアドレス")
    password: str = Field(..., min_length=1, description="パスワード")

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value: str) -> str:
        return _validate_password_byte_length(value)


class TokenResponse(BaseModel):
    """ログインエンドポイントの応答（JWTアクセストークン）。"""

    access_token: str = Field(..., description="JWTアクセストークン")
    token_type: str = Field(default="bearer")
    expires_at: datetime = Field(..., description="トークンの有効期限（UTC）")


class ProjectCreateRequest(BaseModel):
    """プロジェクト新規作成エンドポイント（POST /api/v1/projects）への入力。"""

    title: str = Field(..., min_length=1, description="プロジェクトのタイトル")


class ProjectCreateResponse(BaseModel):
    """プロジェクト新規作成エンドポイントの応答。

    画面を開いた直後にそのまま /chat 等を呼び出せるよう、
    project_id と session_id の両方を返す。
    """

    project_id: str = Field(..., description="作成されたプロジェクトのID")
    session_id: str = Field(..., description="自動作成されたセッションのID（current_state=HEARING）")
    title: str
    current_state: str


class ProjectListItem(BaseModel):
    """プロジェクト一覧エンドポイント（GET /api/v1/projects）の1件分。

    紐づく最新セッションの状態も併せて返し、フロントエンドが一覧から
    直接そのセッションへ復帰できるようにする。
    """

    project_id: str
    title: str
    created_at: datetime
    session_id: str
    current_state: str
    updated_at: datetime
    claude_handoff_prompt: Optional[str] = Field(
        default=None,
        description=(
            "current_stateがCLAUDE_REVIEWの場合のみ、既存messages履歴から導出した"
            "直近のハンドオフプロンプト（画面リロード時の復元用）。それ以外はnull。"
        ),
    )
    claude_json_content: Optional[str] = Field(
        default=None,
        description=(
            "current_stateがCOMPLETEDの場合のみ、既存messages履歴から導出した"
            "確定済みJSON（整形済み文字列、画面リロード時の復元・編集UI用）。"
            "submit_claude_jsonまたはedit_claude_jsonが保存した最新のrole=system"
            "メッセージに対応する。それ以外はnull。"
        ),
    )
