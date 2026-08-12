"""browser lite remote nodes

Revision ID: ce0024
Revises: ce0023
Create Date: 2026-08-12

"""
from typing import Sequence, Union

from alembic import op

revision: str = "ce0024"
down_revision: Union[str, None] = "ce0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS browser_lite_nodes (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            display_name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL DEFAULT '',
            architecture TEXT NOT NULL DEFAULT '',
            app_version TEXT NOT NULL DEFAULT '',
            chromium_version TEXT NOT NULL DEFAULT '',
            capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'offline',
            last_seen_at TIMESTAMP WITH TIME ZONE,
            created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS browser_lite_pairing_codes (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            code_hash TEXT NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            used_at TIMESTAMP WITH TIME ZONE,
            created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS browser_lite_node_id "
        "TEXT REFERENCES browser_lite_nodes(id) ON DELETE SET NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS browser_lite_node_commands (
            id TEXT PRIMARY KEY,
            node_id TEXT NOT NULL REFERENCES browser_lite_nodes(id) ON DELETE CASCADE,
            instance_id TEXT NOT NULL,
            action TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            status TEXT NOT NULL DEFAULT 'pending',
            response JSONB,
            error TEXT,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_browser_lite_nodes_tenant ON browser_lite_nodes(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_browser_lite_pairing_codes_hash ON browser_lite_pairing_codes(code_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sessions_browser_lite_node ON sessions(browser_lite_node_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_browser_lite_node_commands_pending "
        "ON browser_lite_node_commands(node_id, created_at) WHERE status = 'pending'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS browser_lite_node_commands")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS browser_lite_node_id")
    op.execute("DROP TABLE IF EXISTS browser_lite_pairing_codes")
    op.execute("DROP TABLE IF EXISTS browser_lite_nodes")
