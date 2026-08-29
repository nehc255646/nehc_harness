"""session work_mode auto|plan

Revision ID: 0005_session_work_mode
Revises: 0004_provider_api_key_env
Create Date: 2026-08-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_session_work_mode"
down_revision: Union[str, None] = "0004_provider_api_key_env"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("work_mode", sa.String(length=16), nullable=False, server_default="auto"),
    )


def downgrade() -> None:
    op.drop_column("sessions", "work_mode")
