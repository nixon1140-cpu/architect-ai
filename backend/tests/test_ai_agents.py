"""services/ai_agents.py のユニットテスト。

Ollamaへの実通信は一切行わず、httpx.AsyncClient.post をモック化して検証する。
仕様書 Step6 要件1参照。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.exceptions import OllamaConnectionError, OllamaModelNotFoundError
from app.services import ai_agents


def _make_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = text
    if json_data is not None:
        response.json.return_value = json_data
    else:
        response.json.side_effect = ValueError("invalid json")
    return response


@pytest.mark.asyncio
async def test_run_hearing_turn_success():
    """Ollamaが正常応答した場合、一次提案（応答テキスト）がそのまま返る。"""
    mock_response = _make_response(200, {"response": "こんにちは、どのようなシステムをお考えですか？"})

    with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_response)):
        reply = await ai_agents.run_hearing_turn("ECサイトを作りたい")

    assert reply == "こんにちは、どのようなシステムをお考えですか？"


@pytest.mark.asyncio
async def test_run_hearing_turn_connect_error_raises_ollama_connection_error():
    """接続エラー(httpx.ConnectError)は OllamaConnectionError へ変換される。"""
    with patch.object(
        httpx.AsyncClient, "post", new=AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    ):
        with pytest.raises(OllamaConnectionError):
            await ai_agents.run_hearing_turn("メッセージ")


@pytest.mark.asyncio
async def test_run_hearing_turn_timeout_raises_ollama_connection_error():
    """タイムアウト(httpx.TimeoutException)は OllamaConnectionError へ変換される。"""
    with patch.object(
        httpx.AsyncClient, "post", new=AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    ):
        with pytest.raises(OllamaConnectionError):
            await ai_agents.run_hearing_turn("メッセージ")


@pytest.mark.asyncio
async def test_run_hearing_turn_404_raises_ollama_model_not_found_error():
    """404応答（Ollamaのモデル未取得時の挙動）は OllamaModelNotFoundError へ変換される。"""
    mock_response = _make_response(404, {"error": "model 'gemma4' not found"})

    with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(OllamaModelNotFoundError) as exc_info:
            await ai_agents.run_hearing_turn("メッセージ")

    assert exc_info.value.model_name == ai_agents.OLLAMA_MODEL


@pytest.mark.asyncio
async def test_run_hearing_turn_server_error_raises_ollama_connection_error():
    """404以外の4xx/5xxエラーは OllamaConnectionError として扱われる（汎用Exceptionに握りつぶさない）。"""
    mock_response = _make_response(500, text="internal server error")

    with patch.object(httpx.AsyncClient, "post", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(OllamaConnectionError):
            await ai_agents.run_hearing_turn("メッセージ")


def test_build_claude_handoff_prompt_includes_proposal_and_json_schema():
    """ハンドオフプロンプトに提案内容と出力JSON形式のひな形が含まれていることを確認する。"""
    prompt = ai_agents.build_claude_handoff_prompt("提案: backend + db + frontend構成")
    assert "提案: backend + db + frontend構成" in prompt
    assert "project_name" in prompt
    assert "services" in prompt
