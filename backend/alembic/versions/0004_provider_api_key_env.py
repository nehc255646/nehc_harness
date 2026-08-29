"""provider api_key_env name

Revision ID: 0004_provider_api_key_env
Revises: 0003_provider_env_key
Create Date: 2026-08-29

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_provider_api_key_env"
down_revision: Union[str, None] = "0003_provider_env_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("providers", sa.Column("api_key_env", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("providers", "api_key_env")
