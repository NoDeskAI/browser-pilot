"""persist network egress config content

Revision ID: ce0023
Revises: ce0022
Create Date: 2026-06-18

"""
from typing import Sequence, Union

from alembic import op

revision: str = "ce0023"
down_revision: Union[str, None] = "ce0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE network_egress_profiles ADD COLUMN IF NOT EXISTS config_text TEXT NOT NULL DEFAULT ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE network_egress_profiles DROP COLUMN IF EXISTS config_text")
