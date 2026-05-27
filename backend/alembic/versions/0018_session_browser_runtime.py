"""session browser runtime

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-26

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS browser_runtime TEXT NOT NULL DEFAULT 'standard_chrome'"
    )
    op.execute(
        "UPDATE sessions SET browser_runtime = 'standard_chrome' WHERE browser_runtime IS NULL OR browser_runtime = ''"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS browser_runtime")
