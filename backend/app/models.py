"""SQLAlchemy 2.0 (同期) によるデータベース定義。

テーブル: projects / sessions / messages
仕様書 5章 参照。current_state は Enum 型とし、不正な状態遷移をサービス層で
弾けるよう、許可された遷移のみを ALLOWED_STATE_TRANSITIONS に列挙する。
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionState(str, enum.Enum):
    HEARING = "HEARING"
    PROPOSED = "PROPOSED"
    CLAUDE_REVIEW = "CLAUDE_REVIEW"
    COMPLETED = "COMPLETED"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# 許可された状態遷移のみを列挙する。ここに存在しない遷移はすべて不正とみなす
# （例: HEARING -> COMPLETED への直接遷移は不可）。
ALLOWED_STATE_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.HEARING: {SessionState.PROPOSED},
    SessionState.PROPOSED: {SessionState.CLAUDE_REVIEW, SessionState.HEARING},
    SessionState.CLAUDE_REVIEW: {SessionState.COMPLETED, SessionState.PROPOSED},
    SessionState.COMPLETED: set(),
}


def is_valid_transition(current: SessionState, target: SessionState) -> bool:
    """current から target への遷移が許可されているかを判定する。"""
    return target in ALLOWED_STATE_TRANSITIONS.get(current, set())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    current_state: Mapped[SessionState] = mapped_column(
        SAEnum(SessionState, name="session_state"),
        nullable=False,
        default=SessionState.HEARING,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    project: Mapped["Project"] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    # SQLiteは主キーの自動採番（ROWIDエイリアス）をリテラルの INTEGER 型にのみ
    # 適用するため、BIGINT のままだと id が採番されずNOT NULL制約違反になる。
    # with_variant によりPostgreSQL上の型（BIGINT/BIGSERIAL）は変えずに
    # SQLite（Step6のpytestで使用）でのみ INTEGER として振る舞わせる。
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(SAEnum(MessageRole, name="message_role"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    session: Mapped["Session"] = relationship(back_populates="messages")
