"""MySQL ORM"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    api_key_from_env: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    api_key_env: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())

    models: Mapped[list[Model]] = relationship(back_populates="provider", cascade="all, delete-orphan")


class Model(Base):
    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider_id", "model_id", name="uq_provider_model"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    context_window: Mapped[int] = mapped_column(Integer, nullable=False, default=128000)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())

    provider: Mapped[Provider] = relationship(back_populates="models")


class AppConfig(Base):
    __tablename__ = "app_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, server_default=func.now())


class ChatSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="New Session")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active|archived|deleted
    model_id: Mapped[int | None] = mapped_column(
        ForeignKey("models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, server_default=func.now(), index=True)

    model: Mapped[Model | None] = relationship()
    messages: Mapped[list[Message]] = relationship(back_populates="session")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_session_id", "session_id"),
        Index("ix_messages_agent_id", "agent_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, default="main")
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[Any] = mapped_column(JSON, nullable=False)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class ToolLog(Base):
    __tablename__ = "tool_logs"
    __table_args__ = (
        Index("ix_tool_logs_session_id", "session_id"),
        Index("ix_tool_logs_tool_call_id", "tool_call_id"),
        Index("ix_tool_logs_message_id", "message_id"),
        Index("ix_tool_logs_agent_id", "agent_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    tool_call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, default="main")
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    args: Mapped[Any] = mapped_column(JSON, nullable=False)
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_hit: Mapped[str | None] = mapped_column(String(256), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, default="config_allow")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())


class SubAgentRun(Base):
    __tablename__ = "subagent_runs"
    __table_args__ = (
        Index("ix_subagent_runs_main_session_id", "main_session_id"),
        Index("ix_subagent_runs_subagent_id", "subagent_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    main_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    subagent_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # interactive | worker
    behavior_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    late_fed_back: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
