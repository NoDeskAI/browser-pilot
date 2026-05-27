"""agent device level1 contract hardening

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
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
            'audit_' || md5(l.id || '-ownerless-active-blocked'),
            l.tenant_id,
            'system:level1_contract_migration',
            NULL,
            l.device_instance_id,
            l.id,
            l.task_id,
            l.session_id,
            'revoke_ownerless_active_lease',
            'succeeded',
            'internal',
            'browser_pilot',
            'Ownerless active lease revoked for Level 1 Device Governance',
            '[]'::jsonb,
            jsonb_build_object(
                'invalidatedReason', 'ownerless_active_blocked',
                'evidenceStatus', 'not_required',
                'operatorSubject', l.operator_subject
            ),
            NULL,
            NOW()
        FROM agent_device_leases l
        WHERE l.status = 'active'
          AND l.operator_owner_user_id IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM agent_device_audit_events ae
              WHERE ae.id = 'audit_' || md5(l.id || '-ownerless-active-blocked')
          )
        """
    )
    op.execute(
        """
        UPDATE agent_device_leases
        SET status = 'revoked',
            invalidated_reason = 'ownerless_active_blocked',
            updated_at = NOW()
        WHERE status = 'active'
          AND operator_owner_user_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE agent_device_leases
        SET status = 'active',
            invalidated_reason = NULL,
            updated_at = NOW()
        WHERE status = 'revoked'
          AND invalidated_reason = 'ownerless_active_blocked'
          AND NOT EXISTS (
              SELECT 1
              FROM agent_device_leases active
              WHERE active.device_instance_id = agent_device_leases.device_instance_id
                AND active.status = 'active'
          )
        """
    )
    op.execute(
        """
        DELETE FROM agent_device_audit_events
        WHERE action = 'revoke_ownerless_active_lease'
          AND actor = 'system:level1_contract_migration'
        """
    )
