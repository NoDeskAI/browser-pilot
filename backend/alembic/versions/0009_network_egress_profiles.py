"""network egress profiles

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-28

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS network_egress_profiles (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('direct', 'external_proxy', 'clash', 'openvpn')),
        status TEXT NOT NULL DEFAULT 'unchecked',
        proxy_url TEXT DEFAULT '',
        config_ref TEXT DEFAULT '',
        health_error TEXT DEFAULT '',
        last_checked_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS network_egress_id TEXT")
    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_sessions_network_egress_id'
      ) THEN
        ALTER TABLE sessions
          ADD CONSTRAINT fk_sessions_network_egress_id
          FOREIGN KEY (network_egress_id)
          REFERENCES network_egress_profiles(id)
          ON DELETE SET NULL;
      END IF;
    END $$;
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_network_egress_profiles_tenant ON network_egress_profiles(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sessions_network_egress ON sessions(network_egress_id)")


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS fk_sessions_network_egress_id")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS network_egress_id")
    op.execute("DROP TABLE IF EXISTS network_egress_profiles")
