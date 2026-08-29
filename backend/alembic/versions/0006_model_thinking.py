"""model request_thinking / reasoning_effort

Revision ID: 0006_model_thinking
Revises: 0005_session_work_mode
Create Date: 2026-08-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_model_thinking"
down_revision: Union[str, None] = "0005_session_work_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column("request_thinking", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("models", sa.Column("reasoning_effort", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("models", "reasoning_effort")
    op.drop_column("models", "request_thinking")
