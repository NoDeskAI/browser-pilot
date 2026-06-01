"""session runtime placements

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-01

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS session_runtime_placements (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            runtime_provider TEXT NOT NULL,
            runtime_namespace TEXT NOT NULL,
            runtime_pod_name TEXT,
            runtime_service_name TEXT,
            runtime_node_name TEXT,
            runtime_class TEXT NOT NULL,
            placement_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
            node_pool TEXT,
            node_selector JSONB NOT NULL DEFAULT '{}'::jsonb,
            tolerations JSONB NOT NULL DEFAULT '[]'::jsonb,
            runtime_phase TEXT NOT NULL CHECK (
                runtime_phase IN (
                    'provisioning',
                    'starting',
                    'ready',
                    'stopping',
                    'failed',
                    'reclaiming'
                )
            ),
            egress_gateway_pod_name TEXT,
            network_policy_name TEXT,
            secret_name TEXT,
            config_map_name TEXT,
            image_ref TEXT,
            image_digest TEXT,
            requested_cpu TEXT,
            requested_memory TEXT,
            requested_ephemeral_storage TEXT,
            failure_reason TEXT,
            failure_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ready_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            last_heartbeat_at TIMESTAMPTZ,
            last_reconciled_at TIMESTAMPTZ,
            last_error TEXT
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_session_runtime_placements_current
        ON session_runtime_placements(session_id)
        WHERE ended_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_session_runtime_placements_tenant_phase
        ON session_runtime_placements(tenant_id, runtime_phase)
        WHERE ended_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_session_runtime_placements_namespace
        ON session_runtime_placements(runtime_namespace)
        WHERE ended_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_session_runtime_placements_namespace")
    op.execute("DROP INDEX IF EXISTS idx_session_runtime_placements_tenant_phase")
    op.execute("DROP INDEX IF EXISTS uq_session_runtime_placements_current")
    op.execute("DROP TABLE IF EXISTS session_runtime_placements")
