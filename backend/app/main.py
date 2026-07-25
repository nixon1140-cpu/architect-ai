"""FastAPIアプリケーション本体（APIルーター）。

3つのエンドポイント（chat / submit_claude_json / export）と、
services層(ai_agents/iac_builder)・core層(exceptions/logging)が送出する
カスタム例外をHTTPステータスへマッピングする例外ハンドラを定義する。
仕様書 10章 参照。

DB接続:
    仕様書の確定設計判断により、同期SQLAlchemy + run_in_threadpool で統一する。
    ディレクトリ構成に専用の db.py が存在しないため、エンジン/セッション生成は
    このファイルに集約する。
"""

import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import (
    ClaudeJSONValidationError,
    InvalidStateTransitionError,
    OllamaConnectionError,
    OllamaModelNotFoundError,
)
from app.core.logging import get_logger, setup_logging
from app.models import (
    Base,
    Message,
    MessageRole,
    Project,
)
from app.models import Session as SessionModel
from app.models import SessionState, is_valid_transition
from app.schemas import (
    ChatMessageRequest,
    ChatMessageResponse,
    ClaudeJSONSubmission,
    ClaudeRawSubmission,
    ClaudeSubmissionResponse,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectListItem,
    strip_json_fence,
)
from app.services.ai_agents import build_claude_handoff_prompt, run_hearing_turn
from app.services.iac_builder import build_iac_zip

setup_logging()
logger = get_logger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://architect:architect@db:5432/architectai"
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 開発用途のリーンな構成のため、Alembic等は使わずcreate_allでテーブルを用意する。
    Base.metadata.create_all(bind=engine)
    logger.info("ArchitectAI backend起動完了")
    yield


app = FastAPI(title="ArchitectAI", lifespan=lifespan)

# CORSは http://localhost:4500 のみ許可（仕様書 10章）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4500"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 例外ハンドラ: services層のカスタム例外を適切なHTTPステータスへマッピングする。
# 汎用500への握りつぶしは行わない。
# ---------------------------------------------------------------------------


@app.exception_handler(OllamaConnectionError)
async def handle_ollama_connection_error(request: Request, exc: OllamaConnectionError) -> JSONResponse:
    logger.error("Ollama接続エラー (502)", extra={"path": str(request.url)})
    return JSONResponse(
        status_code=502,
        content={"error": "ollama_connection_error", "message": exc.message},
    )


@app.exception_handler(OllamaModelNotFoundError)
async def handle_ollama_model_not_found_error(
    request: Request, exc: OllamaModelNotFoundError
) -> JSONResponse:
    logger.error(
        "Ollamaモデル不在エラー (404)", extra={"path": str(request.url), "model": exc.model_name}
    )
    return JSONResponse(
        status_code=404,
        content={"error": "ollama_model_not_found", "message": exc.message, "model": exc.model_name},
    )


@app.exception_handler(ClaudeJSONValidationError)
async def handle_claude_json_validation_error(
    request: Request, exc: ClaudeJSONValidationError
) -> JSONResponse:
    logger.warning(
        "Claude JSONバリデーションエラー (422)",
        extra={"path": str(request.url)},
    )
    return JSONResponse(
        status_code=422,
        content={"error": "claude_json_validation_error", "message": exc.message, "errors": exc.errors},
    )


@app.exception_handler(InvalidStateTransitionError)
async def handle_invalid_state_transition_error(
    request: Request, exc: InvalidStateTransitionError
) -> JSONResponse:
    logger.warning(
        "不正な状態遷移エラー (409)",
        extra={"path": str(request.url)},
    )
    return JSONResponse(
        status_code=409,
        content={
            "error": "invalid_state_transition",
            "message": exc.message,
            "current_state": exc.current_state,
            "target_state": exc.target_state,
        },
    )


# ---------------------------------------------------------------------------
# DBアクセス用の同期ヘルパー（run_in_threadpool経由で呼び出す）。
# ---------------------------------------------------------------------------


def _get_session_sync(db: DBSession, session_id: uuid.UUID) -> SessionModel:
    session = db.get(SessionModel, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"session '{session_id}' が見つかりません。")
    return session


def _persist_message_sync(
    db: DBSession, session_id: uuid.UUID, role: MessageRole, content: str
) -> Message:
    message = Message(session_id=session_id, role=role, content=content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _transition_state_sync(db: DBSession, session: SessionModel, target_state: SessionState) -> None:
    if not is_valid_transition(session.current_state, target_state):
        raise InvalidStateTransitionError(session.current_state.value, target_state.value)
    session.current_state = target_state
    db.add(session)
    db.commit()
    db.refresh(session)


def _get_latest_message_by_role_sync(
    db: DBSession, session_id: uuid.UUID, role: MessageRole
) -> Optional[Message]:
    stmt = (
        select(Message)
        .where(Message.session_id == session_id, Message.role == role)
        .order_by(Message.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def _list_projects_sync(
    db: DBSession,
) -> list[tuple[Project, SessionModel, Optional[str], Optional[str]]]:
    """プロジェクト一覧を、各プロジェクトの最新セッションと合わせて取得する。

    現行仕様では1プロジェクトにつき常にセッションが1件（作成時に自動生成）
    しか存在しないが、将来の複数セッション化を見越して「最新のupdated_at」の
    セッションを選ぶ形にしておく。

    セッションがCLAUDE_REVIEW状態の場合のみ、画面リロード時の復元用に
    直近のClaude Webハンドオフプロンプト（chatエンドポイントが既に
    role=systemとしてmessagesへ保存している）を合わせて返す。
    セッションがCOMPLETED状態の場合のみ、確定済みJSON（整形済み文字列）を
    合わせて返す（submit_claude_json、または後続のedit_claude_jsonが
    保存した最新のrole=systemメッセージ。/exportと同じ「最新のrole=system」
    という考え方を踏襲している）。CLAUDE_REVIEWとCOMPLETEDはcurrent_stateで
    完全に排他なので、両者のrole=systemメッセージが混同されることはない。
    新規のDBスキーマ変更は行わず、既存のmessagesテーブル・既存role値の
    読み取りのみで実現している。
    """
    latest_session_subq = (
        select(
            SessionModel.project_id,
            func.max(SessionModel.updated_at).label("max_updated_at"),
        )
        .group_by(SessionModel.project_id)
        .subquery()
    )
    stmt = (
        select(Project, SessionModel)
        .join(SessionModel, SessionModel.project_id == Project.id)
        .join(
            latest_session_subq,
            (SessionModel.project_id == latest_session_subq.c.project_id)
            & (SessionModel.updated_at == latest_session_subq.c.max_updated_at),
        )
        .order_by(SessionModel.updated_at.desc())
    )
    rows = db.execute(stmt).all()

    results: list[tuple[Project, SessionModel, Optional[str], Optional[str]]] = []
    for project, session in rows:
        handoff_prompt: Optional[str] = None
        claude_json_content: Optional[str] = None

        if session.current_state == SessionState.CLAUDE_REVIEW:
            latest_system_message = _get_latest_message_by_role_sync(
                db, session.id, MessageRole.SYSTEM
            )
            handoff_prompt = (
                latest_system_message.content if latest_system_message else None
            )
        elif session.current_state == SessionState.COMPLETED:
            latest_system_message = _get_latest_message_by_role_sync(
                db, session.id, MessageRole.SYSTEM
            )
            if latest_system_message:
                try:
                    claude_json_content = json.dumps(
                        json.loads(latest_system_message.content),
                        indent=2,
                        ensure_ascii=False,
                    )
                except json.JSONDecodeError:
                    # 通常発生しないはずだが、万一に備えて生の内容をそのまま返す。
                    claude_json_content = latest_system_message.content

        results.append((project, session, handoff_prompt, claude_json_content))
    return results


def _create_project_and_session_sync(db: DBSession, title: str) -> tuple[Project, SessionModel]:
    """projectsを1件作成し、続けて紐づくsessionsを1件（current_state=HEARING）自動作成する。"""
    project = Project(title=title)
    db.add(project)
    db.flush()  # session作成前にproject.idを確定させる

    session = SessionModel(project_id=project.id, current_state=SessionState.HEARING)
    db.add(session)
    db.commit()
    db.refresh(project)
    db.refresh(session)
    return project, session


def _get_project_sync(db: DBSession, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"project '{project_id}' が見つかりません。")
    return project


def _delete_project_sync(db: DBSession, project_id: uuid.UUID) -> None:
    """プロジェクトと、紐づくsessions・messagesを削除する。

    models.pyのDB外部キー自体には ondelete="CASCADE" は設定されていない
    （Project.sessions / Session.messages は SQLAlchemy ORM側の
    cascade="all, delete-orphan" のみ）。db.delete(project) をORM経由で
    呼ぶことで、SQLAlchemyがmessages -> sessions -> projectsの順に
    個別のDELETE文を発行する（DBのON DELETE句には依存しない）。
    """
    project = _get_project_sync(db, project_id)
    db.delete(project)
    db.commit()


# ---------------------------------------------------------------------------
# エンドポイント
# ---------------------------------------------------------------------------


@app.post("/api/v1/projects", response_model=ProjectCreateResponse, status_code=201)
async def create_project(
    body: ProjectCreateRequest,
    db: DBSession = Depends(get_db),
) -> ProjectCreateResponse:
    """プロジェクトを新規作成し、紐づくセッション（HEARING状態）を1件自動作成する。

    仕様書には明記されていなかったが、フロントエンドが画面を開いた直後に
    project_id / session_id をそのまま使って /chat を呼び出せるようにするための
    起点エンドポイントとして追加した。
    """
    logger.info("projectsリクエストを受信しました", extra={"title": body.title})

    project, session = await run_in_threadpool(_create_project_and_session_sync, db, body.title)

    logger.info(
        "プロジェクト・セッションを作成しました",
        extra={"session_id": str(session.id), "project_id": str(project.id)},
    )

    return ProjectCreateResponse(
        project_id=str(project.id),
        session_id=str(session.id),
        title=project.title,
        current_state=session.current_state.value,
    )


@app.get("/api/v1/projects", response_model=list[ProjectListItem])
async def list_projects(db: DBSession = Depends(get_db)) -> list[ProjectListItem]:
    """プロジェクト一覧エンドポイント。各プロジェクトの最新セッション状態も併せて返す。

    フロントエンドはこの結果を使って、左パネルの一覧表示と、
    localStorageに保存されたsession_idの生存確認（状態復元）の両方を行う。
    """
    logger.info("projects一覧リクエストを受信しました")

    rows = await run_in_threadpool(_list_projects_sync, db)

    return [
        ProjectListItem(
            project_id=str(project.id),
            title=project.title,
            created_at=project.created_at,
            session_id=str(session.id),
            current_state=session.current_state.value,
            updated_at=session.updated_at,
            claude_handoff_prompt=handoff_prompt,
            claude_json_content=claude_json_content,
        )
        for project, session, handoff_prompt, claude_json_content in rows
    ]


@app.delete("/api/v1/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    db: DBSession = Depends(get_db),
) -> Response:
    """プロジェクト削除エンドポイント。紐づくsessions・messagesも合わせて削除する。"""
    logger.info(
        "project削除リクエストを受信しました",
        extra={"project_id": str(project_id)},
    )

    await run_in_threadpool(_delete_project_sync, db, project_id)

    logger.info(
        "プロジェクトを削除しました（紐づくsessions/messagesも削除）",
        extra={"project_id": str(project_id)},
    )

    return Response(status_code=204)


@app.post("/api/v1/sessions/{session_id}/chat", response_model=ChatMessageResponse)
async def chat(
    session_id: uuid.UUID,
    body: ChatMessageRequest,
    request_proposal: bool = False,
    db: DBSession = Depends(get_db),
) -> ChatMessageResponse:
    """要件定義ヒアリング・提案エンドポイント。

    request_proposal=False（既定）: 通常のヒアリング継続（HEARING状態でのみ許可）。
    request_proposal=True:
        - 現在HEARINGなら、PMに構成案を作成させてPROPOSEDへ遷移する（フェーズ1完了）。
        - 現在PROPOSEDなら、直近の提案をもとにClaude Web提出用プロンプトを作成し
          CLAUDE_REVIEWへ遷移する（フェーズ2ハンドオフ）。
    """
    logger.info(
        "chatリクエストを受信しました",
        extra={"session_id": str(session_id)},
    )

    session = await run_in_threadpool(_get_session_sync, db, session_id)
    await run_in_threadpool(_persist_message_sync, db, session_id, MessageRole.USER, body.message)

    if request_proposal:
        if session.current_state == SessionState.HEARING:
            await run_in_threadpool(_transition_state_sync, db, session, SessionState.PROPOSED)
            logger.info(
                "状態遷移: HEARING -> PROPOSED",
                extra={"session_id": str(session_id)},
            )
            reply = await run_hearing_turn(body.message, str(session_id))
            await run_in_threadpool(_persist_message_sync, db, session_id, MessageRole.ASSISTANT, reply)
            return ChatMessageResponse(
                reply=reply, current_state=SessionState.PROPOSED.value, claude_handoff_prompt=None
            )

        if session.current_state == SessionState.PROPOSED:
            await run_in_threadpool(_transition_state_sync, db, session, SessionState.CLAUDE_REVIEW)
            logger.info(
                "状態遷移: PROPOSED -> CLAUDE_REVIEW",
                extra={"session_id": str(session_id)},
            )
            latest_proposal = await run_in_threadpool(
                _get_latest_message_by_role_sync, db, session_id, MessageRole.ASSISTANT
            )
            if latest_proposal is None:
                raise InvalidStateTransitionError(
                    SessionState.PROPOSED.value, SessionState.CLAUDE_REVIEW.value
                )

            handoff_prompt = build_claude_handoff_prompt(latest_proposal.content)
            await run_in_threadpool(
                _persist_message_sync, db, session_id, MessageRole.SYSTEM, handoff_prompt
            )
            return ChatMessageResponse(
                reply="Claude Webへの提出用プロンプトを生成しました。下記をコピーしてClaude Webに貼り付けてください。",
                current_state=SessionState.CLAUDE_REVIEW.value,
                claude_handoff_prompt=handoff_prompt,
            )

        raise InvalidStateTransitionError(session.current_state.value, SessionState.PROPOSED.value)

    # 通常のヒアリング継続は HEARING 状態でのみ許可する。
    if session.current_state != SessionState.HEARING:
        raise InvalidStateTransitionError(session.current_state.value, session.current_state.value)

    reply = await run_hearing_turn(body.message, str(session_id))
    await run_in_threadpool(_persist_message_sync, db, session_id, MessageRole.ASSISTANT, reply)
    return ChatMessageResponse(
        reply=reply, current_state=SessionState.HEARING.value, claude_handoff_prompt=None
    )


@app.post(
    "/api/v1/sessions/{session_id}/submit_claude_json", response_model=ClaudeSubmissionResponse
)
async def submit_claude_json(
    session_id: uuid.UUID,
    body: ClaudeRawSubmission,
    db: DBSession = Depends(get_db),
) -> ClaudeSubmissionResponse:
    """Claude Web出力の受信・検証エンドポイント。

    ```json フェンスの除去 -> JSONパース -> Pydanticバリデーションの順に処理し、
    いずれかに失敗した場合は具体的な異常箇所を含む ClaudeJSONValidationError を送出する。
    """
    logger.info(
        "submit_claude_jsonリクエストを受信しました",
        extra={"session_id": str(session_id)},
    )

    session = await run_in_threadpool(_get_session_sync, db, session_id)

    if session.current_state != SessionState.CLAUDE_REVIEW:
        raise InvalidStateTransitionError(session.current_state.value, SessionState.COMPLETED.value)

    cleaned_text = strip_json_fence(body.raw_text)

    try:
        parsed = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "Claude JSON解析エラー（フェンス除去後もJSONとして不正）",
            extra={"session_id": str(session_id)},
        )
        raise ClaudeJSONValidationError(
            f"JSONとして解析できませんでした: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from exc

    try:
        submission = ClaudeJSONSubmission(**parsed)
    except PydanticValidationError as exc:
        logger.warning(
            "Claude JSONバリデーションエラー（フィールド/型不正）",
            extra={"session_id": str(session_id)},
        )
        raise ClaudeJSONValidationError(
            "Claude Web出力JSONのバリデーションに失敗しました。",
            # include_context=False: カスタムバリデータがValueErrorで送出した場合、
            # デフォルトのerrors()はctx内に生の例外オブジェクトを含めてしまい、
            # JSONResponseへのシリアライズ時にTypeErrorでクラッシュするため除外する。
            errors=exc.errors(include_context=False),
        ) from exc

    await run_in_threadpool(
        _persist_message_sync,
        db,
        session_id,
        MessageRole.SYSTEM,
        submission.model_dump_json(),
    )
    await run_in_threadpool(_transition_state_sync, db, session, SessionState.COMPLETED)
    logger.info(
        "状態遷移: CLAUDE_REVIEW -> COMPLETED",
        extra={"session_id": str(session_id)},
    )

    return ClaudeSubmissionResponse(status="ok", current_state=SessionState.COMPLETED.value)


@app.put(
    "/api/v1/sessions/{session_id}/edit_claude_json", response_model=ClaudeSubmissionResponse
)
async def edit_claude_json(
    session_id: uuid.UUID,
    body: ClaudeRawSubmission,
    db: DBSession = Depends(get_db),
) -> ClaudeSubmissionResponse:
    """確定済みJSONの直接編集・再生成エンドポイント（仕様書 Step2）。

    Claude Web等によるレビュー（Human-in-the-Loop）を意図的にバイパスし、
    ユーザーが確定済みJSONを直接書き換えられるようにする設計判断であり、
    追加の承認フローや確認ダイアログはここでは挿入しない。

    対象セッションがCOMPLETED状態であることを確認したうえで、
    submit_claude_jsonと同じバリデーション（```json フェンスの除去 ->
    JSONパース -> Pydanticバリデーション、services[].name等の既存の
    サニタイズを含む）を再適用する。検証成功後は新しいrole=systemメッセージ
    として追記保存する（既存の確定JSONメッセージは削除せず履歴として残す。
    /exportは既存仕様通り常に最新のrole=systemメッセージを使う）。
    状態遷移は発生しない（COMPLETED状態のまま）。
    """
    logger.info(
        "edit_claude_jsonリクエストを受信しました",
        extra={"session_id": str(session_id)},
    )

    session = await run_in_threadpool(_get_session_sync, db, session_id)

    if session.current_state != SessionState.COMPLETED:
        raise InvalidStateTransitionError(session.current_state.value, SessionState.COMPLETED.value)

    cleaned_text = strip_json_fence(body.raw_text)

    try:
        parsed = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        logger.warning(
            "edit_claude_json JSON解析エラー（フェンス除去後もJSONとして不正）",
            extra={"session_id": str(session_id)},
        )
        raise ClaudeJSONValidationError(
            f"JSONとして解析できませんでした: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from exc

    try:
        submission = ClaudeJSONSubmission(**parsed)
    except PydanticValidationError as exc:
        logger.warning(
            "edit_claude_json バリデーションエラー（フィールド/型不正）",
            extra={"session_id": str(session_id)},
        )
        raise ClaudeJSONValidationError(
            "編集後のJSONのバリデーションに失敗しました。",
            # include_context=False: submit_claude_jsonと同じ理由
            # （生の例外オブジェクトを含めるとJSONResponseへのシリアライズで
            # TypeErrorになるため除外する）。
            errors=exc.errors(include_context=False),
        ) from exc

    await run_in_threadpool(
        _persist_message_sync,
        db,
        session_id,
        MessageRole.SYSTEM,
        submission.model_dump_json(),
    )
    logger.info(
        "確定済みJSONを編集・再保存しました（current_stateはCOMPLETEDのまま変更なし）",
        extra={"session_id": str(session_id)},
    )

    return ClaudeSubmissionResponse(status="ok", current_state=SessionState.COMPLETED.value)


@app.get("/api/v1/sessions/{session_id}/export")
async def export(session_id: uuid.UUID, db: DBSession = Depends(get_db)) -> Response:
    """確定した構成のZIPダウンロードエンドポイント。"""
    logger.info(
        "exportリクエストを受信しました",
        extra={"session_id": str(session_id)},
    )

    session = await run_in_threadpool(_get_session_sync, db, session_id)

    if session.current_state != SessionState.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=(
                f"セッションが完了していません（現在の状態: {session.current_state.value}）。"
                "先に submit_claude_json を完了させてください。"
            ),
        )

    latest_submission_message = await run_in_threadpool(
        _get_latest_message_by_role_sync, db, session_id, MessageRole.SYSTEM
    )
    if latest_submission_message is None:
        raise HTTPException(status_code=400, detail="提出済みの構成JSONが見つかりません。")

    submission_data = json.loads(latest_submission_message.content)
    submission = ClaudeJSONSubmission(**submission_data)

    zip_bytes = build_iac_zip(submission)
    logger.info(
        "ZIPを生成しました",
        extra={"session_id": str(session_id)},
    )

    filename = f"{submission.project_name}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
