"""fingerprint pool table

Revision ID: 0005
Revises: 0004
Create Date: 2025-04-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS fingerprint_pool (
        id          TEXT PRIMARY KEY,
        tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        group_name  TEXT NOT NULL,
        label       TEXT NOT NULL,
        data        JSONB NOT NULL,
        tags        TEXT[] NOT NULL DEFAULT '{}',
        enabled     BOOLEAN NOT NULL DEFAULT true,
        sort_order  INTEGER NOT NULL DEFAULT 0,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(tenant_id, group_name, label)
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fp_pool_tenant_group "
        "ON fingerprint_pool(tenant_id, group_name, enabled)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_fp_pool_tenant_group")
    op.execute("DROP TABLE IF EXISTS fingerprint_pool")
