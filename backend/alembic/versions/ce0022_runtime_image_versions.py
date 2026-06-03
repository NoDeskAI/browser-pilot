"""runtime image versions

Revision ID: ce0022
Revises: 0021
Create Date: 2026-06-03

"""
from typing import Sequence, Union

from alembic import op

revision: str = "ce0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE browser_images ADD COLUMN IF NOT EXISTS runtime TEXT NOT NULL DEFAULT 'standard_chrome'"
    )
    op.execute(
        "ALTER TABLE browser_images ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT ''"
    )
    op.execute(
        "UPDATE browser_images SET runtime = 'standard_chrome' WHERE runtime IS NULL OR runtime = ''"
    )
    op.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS browser_image_id TEXT DEFAULT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS browser_image_id")
    op.execute("ALTER TABLE browser_images DROP COLUMN IF EXISTS name")
    op.execute("ALTER TABLE browser_images DROP COLUMN IF EXISTS runtime")
