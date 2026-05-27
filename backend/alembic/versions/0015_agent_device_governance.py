"""agent device governance

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_device_leases (
            id TEXT PRIMARY KEY,
            device_instance_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            device_type TEXT NOT NULL DEFAULT 'browser_session',
            lease_mode TEXT NOT NULL CHECK (lease_mode IN ('session_bound', 'task_bound')),
            task_id TEXT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            tenant_id TEXT,
            operator_subject TEXT NOT NULL,
            operator_owner_user_id TEXT,
            current_operator TEXT NOT NULL,
            authorized_operators JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'released', 'reclaimed', 'expired', 'revoked')),
            expires_at TIMESTAMPTZ,
            released_at TIMESTAMPTZ,
            reclaimed_at TIMESTAMPTZ,
            invalidated_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_device_leases_one_active
        ON agent_device_leases(device_instance_id)
        WHERE status = 'active'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_device_leases_session_created
        ON agent_device_leases(session_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_device_leases_tenant_status
        ON agent_device_leases(tenant_id, status)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_device_audit_events (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            actor TEXT NOT NULL,
            actor_owner_user_id TEXT,
            device_instance_id TEXT NOT NULL,
            lease_id TEXT,
            task_id TEXT,
            session_id TEXT NOT NULL,
            action TEXT NOT NULL,
            outcome TEXT NOT NULL,
            side_effect_level TEXT NOT NULL,
            audit_boundary TEXT NOT NULL,
            summary TEXT NOT NULL,
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_device_audit_device_created
        ON agent_device_audit_events(device_instance_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_device_audit_tenant_created
        ON agent_device_audit_events(tenant_id, created_at DESC)
        """
    )
    op.execute(
        """
        INSERT INTO agent_device_leases (
            id,
            device_instance_id,
            lease_mode,
            task_id,
            session_id,
            tenant_id,
            operator_subject,
            operator_owner_user_id,
            current_operator,
            authorized_operators,
            status,
            created_at,
            updated_at
        )
        SELECT
            'lease_' || md5(s.id || '-agent-device-initial'),
            s.id,
            'session_bound',
            NULL,
            s.id,
            s.tenant_id,
            CASE
                WHEN s.user_id IS NOT NULL THEN 'user' || chr(58) || s.user_id
                ELSE 'system' || chr(58) || 'migration'
            END,
            s.user_id,
            CASE
                WHEN s.user_id IS NOT NULL THEN 'user' || chr(58) || s.user_id
                ELSE 'system' || chr(58) || 'migration'
            END,
            '[]'::jsonb,
            'active',
            COALESCE(s.created_at, NOW()),
            NOW()
        FROM sessions s
        WHERE NOT EXISTS (
            SELECT 1
            FROM agent_device_leases l
            WHERE l.device_instance_id = s.id
              AND l.status = 'active'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_device_audit_tenant_created")
    op.execute("DROP INDEX IF EXISTS idx_agent_device_audit_device_created")
    op.execute("DROP TABLE IF EXISTS agent_device_audit_events")
    op.execute("DROP INDEX IF EXISTS idx_agent_device_leases_tenant_status")
    op.execute("DROP INDEX IF EXISTS idx_agent_device_leases_session_created")
    op.execute("DROP INDEX IF EXISTS idx_agent_device_leases_one_active")
    op.execute("DROP TABLE IF EXISTS agent_device_leases")
