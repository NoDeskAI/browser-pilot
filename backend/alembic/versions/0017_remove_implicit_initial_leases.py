"""remove implicit initial session leases

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-25

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
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
            'audit_' || md5(l.id || '-implicit-initial-lease-removed'),
            l.tenant_id,
            'system:initial_lease_cleanup',
            NULL,
            l.device_instance_id,
            l.id,
            l.task_id,
            l.session_id,
            'revoke_implicit_initial_lease',
            'succeeded',
            'internal',
            'browser_pilot',
            'Implicit initial session lease revoked; sessions now start idle until an operator explicitly acquires a lease',
            '[]'::jsonb,
            jsonb_build_object(
                'invalidatedReason', 'implicit_initial_lease_removed',
                'evidenceStatus', 'not_required',
                'operatorSubject', l.operator_subject
            ),
            NULL,
            NOW()
        FROM agent_device_leases l
        WHERE l.status = 'active'
          AND l.lease_mode = 'session_bound'
          AND l.task_id IS NULL
          AND l.expires_at IS NULL
          AND (
              l.id = 'lease_' || md5(l.device_instance_id || '-agent-device-initial')
              OR EXISTS (
                  SELECT 1
                  FROM agent_device_audit_events ae
                  WHERE ae.lease_id = l.id
                    AND ae.action = 'reserve_device'
                    AND ae.summary = 'Initial session-bound device lease created'
              )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM agent_device_audit_events existing
              WHERE existing.id = 'audit_' || md5(l.id || '-implicit-initial-lease-removed')
          )
        """
    )
    op.execute(
        """
        UPDATE agent_device_leases l
        SET status = 'revoked',
            invalidated_reason = 'implicit_initial_lease_removed',
            updated_at = NOW()
        WHERE l.status = 'active'
          AND l.lease_mode = 'session_bound'
          AND l.task_id IS NULL
          AND l.expires_at IS NULL
          AND (
              l.id = 'lease_' || md5(l.device_instance_id || '-agent-device-initial')
              OR EXISTS (
                  SELECT 1
                  FROM agent_device_audit_events ae
                  WHERE ae.lease_id = l.id
                    AND ae.action = 'reserve_device'
                    AND ae.summary = 'Initial session-bound device lease created'
              )
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE agent_device_leases l
        SET status = 'active',
            invalidated_reason = NULL,
            updated_at = NOW()
        WHERE l.status = 'revoked'
          AND l.invalidated_reason = 'implicit_initial_lease_removed'
          AND NOT EXISTS (
              SELECT 1
              FROM agent_device_leases active
              WHERE active.device_instance_id = l.device_instance_id
                AND active.status = 'active'
          )
        """
    )
    op.execute(
        """
        DELETE FROM agent_device_audit_events
        WHERE action = 'revoke_implicit_initial_lease'
          AND actor = 'system:initial_lease_cleanup'
        """
    )
