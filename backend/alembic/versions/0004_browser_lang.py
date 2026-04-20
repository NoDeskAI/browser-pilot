"""add browser_lang column to sessions

Revision ID: 0004
Revises: 0003
Create Date: 2025-04-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS browser_lang VARCHAR(16) NOT NULL DEFAULT 'zh-CN'")


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS browser_lang")
