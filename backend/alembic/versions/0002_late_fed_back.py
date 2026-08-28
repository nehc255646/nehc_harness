"""late subagent results fed back on resume

Revision ID: 0002_late_fed_back
Revises: 0001_m3
Create Date: 2026-08-28

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_late_fed_back"
down_revision: Union[str, None] = "0001_m3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subagent_runs",
        sa.Column("late_fed_back", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("subagent_runs", "late_fed_back")
