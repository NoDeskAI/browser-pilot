"""runtime capacity control

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-01

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_pools (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            runtime_classes JSONB NOT NULL DEFAULT '[]'::jsonb,
            active_session_capacity INTEGER NOT NULL CHECK (active_session_capacity >= 0),
            is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            is_draining BOOLEAN NOT NULL DEFAULT FALSE,
            drain_reason TEXT,
            drained_by TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            drained_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        INSERT INTO runtime_pools (
            id, name, runtime_classes, active_session_capacity
        )
        VALUES (
            'runtime_pool_default',
            'Default runtime worker pool',
            '["standard_chrome", "cloak_chromium"]'::jsonb,
            100
        )
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_nodes (
            id TEXT PRIMARY KEY,
            runtime_pool_id TEXT NOT NULL REFERENCES runtime_pools(id) ON DELETE CASCADE,
            provider_node_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'draining', 'disabled')),
            labels JSONB NOT NULL DEFAULT '{}'::jsonb,
            capacity JSONB NOT NULL DEFAULT '{}'::jsonb,
            allocatable JSONB NOT NULL DEFAULT '{}'::jsonb,
            drain_reason TEXT,
            drained_by TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            drained_at TIMESTAMPTZ,
            disabled_reason TEXT,
            disabled_by TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            disabled_at TIMESTAMPTZ,
            last_seen_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(runtime_pool_id, provider_node_name)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_nodes_pool_status
        ON runtime_nodes(runtime_pool_id, status)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_capacity_reservations (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            runtime_pool_id TEXT NOT NULL REFERENCES runtime_pools(id) ON DELETE RESTRICT,
            runtime_class TEXT NOT NULL,
            reserved_phase TEXT NOT NULL DEFAULT 'reserved'
                CHECK (reserved_phase IN ('reserved', 'released')),
            reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            released_at TIMESTAMPTZ,
            release_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_runtime_capacity_reservations_current
        ON runtime_capacity_reservations(session_id)
        WHERE released_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_capacity_reservations_pool_active
        ON runtime_capacity_reservations(runtime_pool_id, reserved_phase)
        WHERE released_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_runtime_capacity_reservations_pool_active")
    op.execute("DROP INDEX IF EXISTS uq_runtime_capacity_reservations_current")
    op.execute("DROP TABLE IF EXISTS runtime_capacity_reservations")
    op.execute("DROP INDEX IF EXISTS idx_runtime_nodes_pool_status")
    op.execute("DROP TABLE IF EXISTS runtime_nodes")
    op.execute("DROP TABLE IF EXISTS runtime_pools")
