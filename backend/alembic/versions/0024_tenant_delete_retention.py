"""tenant delete retention

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-01

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE tenant_platform_settings
            ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS purge_requested_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS purge_requested_by TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS purge_request_reason TEXT
        """
    )
    op.execute(
        """
        UPDATE tenant_platform_settings
        SET retention_until = COALESCE(deleted_at, NOW()) + INTERVAL '30 days'
        WHERE status = 'deleted'
          AND retention_until IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tenant_platform_settings_purge_pending
        ON tenant_platform_settings(retention_until)
        WHERE status = 'deleted'
          AND purge_requested_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_tenant_platform_settings_purge_pending")
    op.execute(
        """
        ALTER TABLE tenant_platform_settings
            DROP COLUMN IF EXISTS purge_request_reason,
            DROP COLUMN IF EXISTS purge_requested_by,
            DROP COLUMN IF EXISTS purge_requested_at,
            DROP COLUMN IF EXISTS retention_until
        """
    )
