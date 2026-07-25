"""Ollama (gemma4) を用いた要件定義ヒアリング・提案ロジック。

httpx の非同期クライアントで Ollama API を直接呼び出す（LangChain等の重量フレームワークは使用しない）。
接続失敗・タイムアウト・モデル不在は無言でハングさせず、core.exceptions のカスタム例外として
明示的に送出する。仕様書 3章・9.1章 参照。
"""

import os
from typing import Optional

import httpx

from app.core.exceptions import OllamaConnectionError, OllamaModelNotFoundError
from app.core.logging import get_logger

logger = get_logger(__name__)

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4")
# 実機検証の結果、CPUのみでの推論（GPU非搭載環境）ではPMペルソナの
# システムプロンプトにより長文応答（1000トークン超）が生成され、
# 60秒では不足し実際には約140秒かかるケースを確認したため、余裕を持たせている。
_REQUEST_TIMEOUT_SECONDS = 240.0

_PM_SYSTEM_PROMPT = (
    "あなたは経験豊富なプロダクトマネージャー(PM)です。"
    "ユーザーの曖昧なシステムアイデアについて、機能要件・非機能要件を明確にするための"
    "質問を行い、十分な情報が揃ったら簡潔なシステム構成案（サービス一覧・各サービスの役割）を"
    "提示してください。日本語で回答してください。"
)

_CLAUDE_HANDOFF_TEMPLATE = """あなたはシニアソフトウェアアーキテクトです。
以下のシステム構成案をレビューし、改善点があれば指摘した上で、
最終的なシステム構成を次のJSON形式で出力してください。

出力形式（このキー名を厳守すること）:
{{
  "project_name": "プロジェクト名",
  "services": [
    {{
      "name": "サービス名（英数字・ハイフン・アンダースコアのみ）",
      "type": "frontend/backend/database等",
      "image_or_build": "Dockerイメージ名、またはビルドコンテキストパス（例: ./frontend）",
      "env": {{"KEY": "value"}},
      "ports": ["8000:8000"]
    }}
  ],
  "notes": "補足事項（任意）"
}}

JSON以外の説明文やMarkdownのコードフェンスは含めず、JSONオブジェクトのみを出力してください。

【構成案】
{proposal}
"""


async def _call_ollama_generate(
    prompt: str, system: Optional[str] = None, session_id: Optional[str] = None
) -> str:
    """Ollamaの /api/generate を呼び出し、応答テキストを返す。

    接続失敗・タイムアウトは OllamaConnectionError、
    モデル未取得（404）は OllamaModelNotFoundError として明示的に送出する。
    """
    payload: dict[str, object] = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system

    url = f"{OLLAMA_BASE_URL}/api/generate"
    log_extra = {"session_id": session_id} if session_id else {}

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        logger.warning("Ollamaへの接続がタイムアウトしました", exc_info=True, extra=log_extra)
        raise OllamaConnectionError(f"Ollama API ({url}) への接続がタイムアウトしました。") from exc
    except httpx.ConnectError as exc:
        logger.warning("Ollamaへの接続に失敗しました", exc_info=True, extra=log_extra)
        raise OllamaConnectionError(
            f"Ollama API ({url}) に接続できません。Ollamaが起動しているか確認してください。"
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Ollama呼び出し中に予期しない通信エラーが発生しました", exc_info=True, extra=log_extra)
        raise OllamaConnectionError(f"Ollama API ({url}) との通信中にエラーが発生しました: {exc}") from exc

    if response.status_code == 404:
        # Ollamaはモデル未取得の場合 404 を返す（本プロジェクトでの実機検証に基づく判定）。
        logger.warning("Ollamaモデルが見つかりません: %s", OLLAMA_MODEL, extra=log_extra)
        raise OllamaModelNotFoundError(OLLAMA_MODEL)

    if response.status_code >= 400:
        logger.warning("Ollamaがエラーステータスを返しました: %s", response.status_code, extra=log_extra)
        raise OllamaConnectionError(
            f"Ollama APIがエラーを返しました (status={response.status_code}): {response.text}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        logger.warning("Ollama応答のJSON解析に失敗しました", exc_info=True, extra=log_extra)
        raise OllamaConnectionError("Ollama APIの応答をJSONとして解析できませんでした。") from exc

    reply = data.get("response")
    if reply is None:
        raise OllamaConnectionError("Ollama APIの応答に 'response' フィールドがありませんでした。")

    return reply


async def run_hearing_turn(message: str, session_id: Optional[str] = None) -> str:
    """要件定義ヒアリング（フェーズ1）の1ターンを実行し、PMとしての応答を返す。"""
    return await _call_ollama_generate(prompt=message, system=_PM_SYSTEM_PROMPT, session_id=session_id)


def build_claude_handoff_prompt(proposal: str) -> str:
    """フェーズ1で作成された構成案から、Claude Webへ貼り付けるための提出用プロンプトを組み立てる（フェーズ2）。"""
    return _CLAUDE_HANDOFF_TEMPLATE.format(proposal=proposal)
