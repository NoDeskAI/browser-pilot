"""session viewer tickets

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-31

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS session_viewer_tickets (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            tenant_id TEXT,
            user_id TEXT,
            operator_subject TEXT NOT NULL,
            lease_id TEXT NOT NULL REFERENCES agent_device_leases(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            mode TEXT NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            consumed_at TIMESTAMP WITH TIME ZONE,
            remote_addr TEXT,
            user_agent TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_viewer_tickets_session ON session_viewer_tickets(session_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_viewer_tickets_lease ON session_viewer_tickets(lease_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_session_viewer_tickets_expires ON session_viewer_tickets(expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_viewer_tickets")
