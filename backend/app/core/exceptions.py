"""アプリケーション固有の例外クラス。

Ollama接続失敗・モデル不在などを無言のハング/タイムアウトにせず、
明示的なエラーメッセージとして呼び出し元（main.pyのAPIハンドラ）に伝播させるために使う。
仕様書 3章・9.1章 参照。
"""

from typing import Any, Optional


class ArchitectAIError(Exception):
    """アプリケーション例外の基底クラス。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class OllamaConnectionError(ArchitectAIError):
    """Ollama APIへの接続に失敗した場合（サーバー未起動・タイムアウト等）に送出する。"""


class OllamaModelNotFoundError(ArchitectAIError):
    """指定されたOllamaモデルが取得（pull）されていない場合に送出する。"""

    def __init__(self, model_name: str):
        self.model_name = model_name
        super().__init__(f"Ollamaモデル '{model_name}' が見つかりません。'ollama pull {model_name}' を実行してください。")


class InvalidStateTransitionError(ArchitectAIError):
    """セッションの状態遷移が許可されていない組み合わせだった場合に送出する。"""

    def __init__(self, current_state: str, target_state: str):
        self.current_state = current_state
        self.target_state = target_state
        super().__init__(f"'{current_state}' から '{target_state}' への状態遷移は許可されていません。")


class ClaudeJSONValidationError(ArchitectAIError):
    """Claude Web出力JSONのバリデーションに失敗した場合に送出する。

    errors には、どのフィールド・どの型が不正だったかを具体的に示す詳細情報を保持する。
    """

    def __init__(self, message: str, errors: Optional[list[dict[str, Any]]] = None):
        self.errors = errors or []
        super().__init__(message)
