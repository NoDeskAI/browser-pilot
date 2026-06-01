"""view-only viewer tickets

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-01

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE session_viewer_tickets ALTER COLUMN lease_id DROP NOT NULL")


def downgrade() -> None:
    op.execute("DELETE FROM session_viewer_tickets WHERE lease_id IS NULL")
    op.execute("ALTER TABLE session_viewer_tickets ALTER COLUMN lease_id SET NOT NULL")
