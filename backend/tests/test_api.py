"""主要APIエンドポイントの結合テスト。

DBはconftest.pyでSQLiteインメモリに差し替え済み（本番PostgreSQLへは接続しない）。
Ollama呼び出しは app.main.run_hearing_turn をモック化して隔離する。
仕様書 Step6 要件3参照。
"""

from unittest.mock import AsyncMock, patch

from app.core.exceptions import (
    OllamaConnectionError,
    OllamaModelNotFoundError,
)


def _create_project(client, title: str = "テストプロジェクト") -> dict:
    res = client.post("/api/v1/projects", json={"title": title})
    assert res.status_code == 201, res.text
    return res.json()


# ---------------------------------------------------------------------------
# POST /api/v1/projects
# ---------------------------------------------------------------------------


def test_create_project_success(client):
    body = _create_project(client, "ECサイト構築プロジェクト")

    assert body["title"] == "ECサイト構築プロジェクト"
    assert body["current_state"] == "HEARING"
    assert body["project_id"]
    assert body["session_id"]


def test_create_project_validation_error_returns_422(client):
    """titleが空文字の場合、Pydanticのmin_length違反で422が返る。"""
    res = client.post("/api/v1/projects", json={"title": ""})
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# カスタム例外 -> HTTPステータスマッピング
# ---------------------------------------------------------------------------


def test_chat_ollama_connection_error_returns_502(client):
    project = _create_project(client)
    session_id = project["session_id"]

    with patch(
        "app.main.run_hearing_turn",
        new=AsyncMock(side_effect=OllamaConnectionError("Ollamaに接続できません。")),
    ):
        res = client.post(f"/api/v1/sessions/{session_id}/chat", json={"message": "こんにちは"})

    assert res.status_code == 502
    body = res.json()
    assert body["error"] == "ollama_connection_error"
    assert "message" in body


def test_chat_ollama_model_not_found_returns_404(client):
    project = _create_project(client)
    session_id = project["session_id"]

    with patch(
        "app.main.run_hearing_turn",
        new=AsyncMock(side_effect=OllamaModelNotFoundError("gemma4")),
    ):
        res = client.post(f"/api/v1/sessions/{session_id}/chat", json={"message": "こんにちは"})

    assert res.status_code == 404
    body = res.json()
    assert body["error"] == "ollama_model_not_found"
    assert body["model"] == "gemma4"


def test_submit_claude_json_invalid_state_transition_returns_409(client):
    """HEARING状態のままsubmit_claude_jsonを呼ぶと、CLAUDE_REVIEWでないため409で弾かれる。"""
    project = _create_project(client)
    session_id = project["session_id"]

    res = client.post(
        f"/api/v1/sessions/{session_id}/submit_claude_json",
        json={"raw_text": '{"project_name": "x", "services": [{"name": "a", "type": "b", "image_or_build": "c"}]}'},
    )

    assert res.status_code == 409
    body = res.json()
    assert body["error"] == "invalid_state_transition"
    assert body["current_state"] == "HEARING"


def test_submit_claude_json_validation_error_returns_422(client):
    """CLAUDE_REVIEW状態で、不正な（services[].nameが規約違反な）JSONを提出すると422が返る。"""
    project = _create_project(client)
    session_id = project["session_id"]

    # HEARING -> PROPOSED（Ollama呼び出しをモック化）
    with patch(
        "app.main.run_hearing_turn",
        new=AsyncMock(return_value="構成案: backend + db"),
    ):
        res = client.post(
            f"/api/v1/sessions/{session_id}/chat?request_proposal=true",
            json={"message": "これで構成案をまとめてください"},
        )
    assert res.status_code == 200
    assert res.json()["current_state"] == "PROPOSED"

    # PROPOSED -> CLAUDE_REVIEW（直近の提案からハンドオフプロンプトを作成するのみでOllama呼び出しは無い）
    res = client.post(
        f"/api/v1/sessions/{session_id}/chat?request_proposal=true",
        json={"message": "OK"},
    )
    assert res.status_code == 200
    assert res.json()["current_state"] == "CLAUDE_REVIEW"

    # services[].name に不正な文字（スペース）を含むJSONを提出する。
    invalid_json = (
        '```json\n'
        '{"project_name": "sample", "services": '
        '[{"name": "invalid name!", "type": "backend", "image_or_build": "./backend"}]}\n'
        '```'
    )
    res = client.post(
        f"/api/v1/sessions/{session_id}/submit_claude_json",
        json={"raw_text": invalid_json},
    )

    assert res.status_code == 422
    body = res.json()
    assert body["error"] == "claude_json_validation_error"
    assert len(body["errors"]) > 0


def test_full_happy_path_flow_reaches_completed_and_export(client):
    """HEARING -> PROPOSED -> CLAUDE_REVIEW -> COMPLETED -> export までの一連の流れを検証する。"""
    project = _create_project(client)
    session_id = project["session_id"]

    with patch("app.main.run_hearing_turn", new=AsyncMock(return_value="構成案: backend + db")):
        res = client.post(
            f"/api/v1/sessions/{session_id}/chat?request_proposal=true",
            json={"message": "構成案をお願いします"},
        )
    assert res.json()["current_state"] == "PROPOSED"

    res = client.post(
        f"/api/v1/sessions/{session_id}/chat?request_proposal=true",
        json={"message": "OK"},
    )
    assert res.json()["current_state"] == "CLAUDE_REVIEW"
    assert res.json()["claude_handoff_prompt"]

    valid_json = (
        '{"project_name": "sample-app", "services": '
        '[{"name": "backend", "type": "backend", "image_or_build": "./backend", '
        '"env": {}, "ports": ["8000:8000"]}]}'
    )
    res = client.post(
        f"/api/v1/sessions/{session_id}/submit_claude_json",
        json={"raw_text": valid_json},
    )
    assert res.status_code == 200
    assert res.json()["current_state"] == "COMPLETED"

    res = client.get(f"/api/v1/sessions/{session_id}/export")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert len(res.content) > 0


def test_export_before_completed_returns_400(client):
    project = _create_project(client)
    session_id = project["session_id"]

    res = client.get(f"/api/v1/sessions/{session_id}/export")
    assert res.status_code == 400


def test_chat_with_nonexistent_session_returns_404(client):
    res = client.post(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/chat",
        json={"message": "こんにちは"},
    )
    assert res.status_code == 404
