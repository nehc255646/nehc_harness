"""provider api_key_from_env

Revision ID: 0003_provider_env_key
Revises: 0002_late_fed_back
Create Date: 2026-08-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_provider_env_key"
down_revision: Union[str, None] = "0002_late_fed_back"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "providers",
        sa.Column("api_key_from_env", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("providers", "api_key_from_env")
