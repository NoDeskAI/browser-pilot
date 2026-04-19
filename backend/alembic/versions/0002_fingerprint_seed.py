"""add fingerprint_seed to sessions

Revision ID: 0002
Revises: 0001
Create Date: 2025-04-19

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS fingerprint_seed BIGINT")
    op.execute(
        "UPDATE sessions SET fingerprint_seed = floor(random() * 4294967296)::bigint "
        "WHERE fingerprint_seed IS NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS fingerprint_seed")
