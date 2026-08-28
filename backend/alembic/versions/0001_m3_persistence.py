"""M3 persistence tables

Revision ID: 0001_m3
Revises:
Create Date: 2026-08-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_m3"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id"),
    )
    op.create_table(
        "models",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=False, server_default="128000"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.2"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_provider_model"),
    )
    op.create_index("ix_models_provider_id", "models", ["provider_id"])
    op.create_table(
        "app_config",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False, server_default="New Session"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("model_id", sa.BigInteger(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_model_id", "sessions", ["model_id"])
    op.create_index("ix_sessions_updated_at", "sessions", ["updated_at"])
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False, server_default="main"),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_index("ix_messages_agent_id", "messages", ["agent_id"])
    op.create_table(
        "tool_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False, server_default="main"),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("rule_hit", sa.String(length=256), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False, server_default="config_allow"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_logs_session_id", "tool_logs", ["session_id"])
    op.create_index("ix_tool_logs_tool_call_id", "tool_logs", ["tool_call_id"])
    op.create_index("ix_tool_logs_message_id", "tool_logs", ["message_id"])
    op.create_index("ix_tool_logs_agent_id", "tool_logs", ["agent_id"])
    op.create_table(
        "subagent_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("main_session_id", sa.String(length=36), nullable=False),
        sa.Column("subagent_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("behavior_desc", sa.Text(), nullable=True),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("late", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["main_session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subagent_id"),
    )
    op.create_index("ix_subagent_runs_main_session_id", "subagent_runs", ["main_session_id"])
    op.create_index("ix_subagent_runs_subagent_id", "subagent_runs", ["subagent_id"])


def downgrade() -> None:
    op.drop_table("subagent_runs")
    op.drop_table("tool_logs")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("app_config")
    op.drop_table("models")
    op.drop_table("providers")
