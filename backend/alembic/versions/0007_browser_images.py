"""browser images + sessions.chrome_version

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-23

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS browser_images (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            chrome_major INTEGER NOT NULL,
            chrome_version TEXT NOT NULL DEFAULT '',
            base_image  TEXT NOT NULL,
            image_tag   TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            build_log   TEXT DEFAULT '',
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS chrome_version TEXT DEFAULT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS chrome_version")
    op.execute("DROP TABLE IF EXISTS browser_images")
