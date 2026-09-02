"""session allow_rules JSON

Revision ID: 0007_session_allow_rules
Revises: 0006_model_thinking
Create Date: 2026-09-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_session_allow_rules"
down_revision: Union[str, None] = "0006_model_thinking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("allow_rules", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "allow_rules")
