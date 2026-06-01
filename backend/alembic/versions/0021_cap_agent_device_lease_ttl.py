"""cap agent device lease ttl

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-01

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO agent_device_audit_events (
            id,
            tenant_id,
            actor,
            actor_owner_user_id,
            device_instance_id,
            lease_id,
            task_id,
            session_id,
            action,
            outcome,
            side_effect_level,
            audit_boundary,
            summary,
            evidence_refs,
            details,
            error,
            created_at
        )
        SELECT
            'audit_' || md5(l.id || '-lease-ttl-capped'),
            l.tenant_id,
            'system:lease_ttl_cap_migration',
            NULL,
            l.device_instance_id,
            l.id,
            l.task_id,
            l.session_id,
            'lease_expiration_capped',
            'succeeded',
            'internal',
            'browser_pilot',
            'Device lease capped to the maximum TTL',
            '[]'::jsonb,
            jsonb_build_object(
                'evidenceStatus', 'not_required',
                'maxTtlSeconds', 1800,
                'previousExpiresAt', l.expires_at
            ),
            NULL,
            NOW()
        FROM agent_device_leases l
        WHERE l.status = 'active'
          AND (
              l.expires_at IS NULL
              OR l.expires_at > NOW() + INTERVAL '30 minutes'
              OR l.expires_at > l.updated_at + INTERVAL '30 minutes'
          )
          AND NOT EXISTS (
              SELECT 1
              FROM agent_device_audit_events existing
              WHERE existing.id = 'audit_' || md5(l.id || '-lease-ttl-capped')
          )
        """
    )
    op.execute(
        """
        UPDATE agent_device_leases
        SET expires_at = LEAST(
                COALESCE(expires_at, NOW() + INTERVAL '30 minutes'),
                NOW() + INTERVAL '30 minutes'
            ),
            updated_at = NOW()
        WHERE status = 'active'
          AND (
              expires_at IS NULL
              OR expires_at > NOW() + INTERVAL '30 minutes'
              OR expires_at > updated_at + INTERVAL '30 minutes'
          )
        """
    )
    op.execute(
        """
        UPDATE agent_device_leases
        SET expires_at = updated_at
        WHERE expires_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE agent_device_leases
        SET expires_at = updated_at + INTERVAL '30 minutes'
        WHERE expires_at > updated_at + INTERVAL '30 minutes'
        """
    )
    op.execute("ALTER TABLE agent_device_leases ALTER COLUMN expires_at SET NOT NULL")
    op.execute(
        """
        ALTER TABLE agent_device_leases
        ADD CONSTRAINT chk_agent_device_leases_ttl_cap
        CHECK (expires_at <= updated_at + INTERVAL '30 minutes')
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_device_leases DROP CONSTRAINT IF EXISTS chk_agent_device_leases_ttl_cap")
    op.execute("ALTER TABLE agent_device_leases ALTER COLUMN expires_at DROP NOT NULL")
    op.execute(
        """
        DELETE FROM agent_device_audit_events
        WHERE action = 'lease_expiration_capped'
          AND actor = 'system:lease_ttl_cap_migration'
        """
    )
