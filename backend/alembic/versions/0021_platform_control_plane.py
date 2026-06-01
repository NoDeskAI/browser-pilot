"""platform control plane

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
        CREATE TABLE IF NOT EXISTS platform_users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('platform_admin', 'platform_operator')),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_login_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            default_active_session_limit INTEGER NOT NULL CHECK (default_active_session_limit >= 0),
            default_runtime_class_limits JSONB NOT NULL DEFAULT '{}'::jsonb,
            default_max_session_seconds INTEGER NOT NULL CHECK (default_max_session_seconds > 0),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        INSERT INTO plans (
            id, code, name, default_active_session_limit,
            default_runtime_class_limits, default_max_session_seconds
        )
        VALUES ('plan_default', 'default', 'Default', 3, '{}'::jsonb, 3600)
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_platform_settings (
            tenant_id TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'deleted')),
            runtime_namespace TEXT NOT NULL UNIQUE,
            runtime_image_policy JSONB NOT NULL DEFAULT '{"source":"approved_runtime_images","tenantCustomImages":false}'::jsonb,
            created_by_platform_user_id TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            suspended_at TIMESTAMPTZ,
            suspended_by TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            suspend_reason TEXT,
            deleted_at TIMESTAMPTZ,
            deleted_by TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            delete_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_runtime_quotas (
            tenant_id TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            active_session_limit INTEGER NOT NULL CHECK (active_session_limit >= 0),
            runtime_class_limits JSONB NOT NULL DEFAULT '{}'::jsonb,
            max_session_seconds INTEGER NOT NULL CHECK (max_session_seconds > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            update_reason TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_entitlements (
            tenant_id TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
            plan_id TEXT REFERENCES plans(id) ON DELETE SET NULL,
            active_session_limit_override INTEGER CHECK (active_session_limit_override IS NULL OR active_session_limit_override >= 0),
            runtime_class_limits_override JSONB,
            max_session_seconds_override INTEGER CHECK (max_session_seconds_override IS NULL OR max_session_seconds_override > 0),
            contract_ref TEXT,
            trial_ends_at TIMESTAMPTZ,
            effective_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            effective_until TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_by TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            update_reason TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approved_runtime_images (
            id TEXT PRIMARY KEY,
            runtime_class TEXT NOT NULL,
            image_ref TEXT NOT NULL,
            image_digest TEXT NOT NULL,
            chrome_version TEXT,
            build_id TEXT,
            scan_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (scan_status IN ('pending', 'passed', 'failed')),
            approval_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (approval_status IN ('pending', 'approved', 'rejected', 'revoked')),
            approved_by TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            approved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(runtime_class, image_digest)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approved_runtime_images_lookup
        ON approved_runtime_images(runtime_class, approval_status)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_audit_events (
            id TEXT PRIMARY KEY,
            actor_platform_user_id TEXT REFERENCES platform_users(id) ON DELETE SET NULL,
            actor_role TEXT,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT,
            tenant_id TEXT,
            request_id TEXT,
            outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
            reason TEXT,
            before JSONB,
            after JSONB,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_platform_audit_tenant_created
        ON platform_audit_events(tenant_id, created_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_platform_audit_actor_created
        ON platform_audit_events(actor_platform_user_id, created_at DESC)
        """
    )
    op.execute(
        """
        INSERT INTO tenant_platform_settings (tenant_id, status, runtime_namespace)
        SELECT id, 'active', 'bp-tenant-' || substr(md5(id), 1, 16)
        FROM tenants
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tenant_runtime_quotas (
            tenant_id, active_session_limit, runtime_class_limits,
            max_session_seconds, update_reason
        )
        SELECT id, 3, '{}'::jsonb, 3600, 'migration_default'
        FROM tenants
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tenant_entitlements (tenant_id, plan_id, update_reason)
        SELECT t.id, p.id, 'migration_default'
        FROM tenants t
        LEFT JOIN plans p ON p.code = 'default'
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform_audit_events")
    op.execute("DROP INDEX IF EXISTS idx_approved_runtime_images_lookup")
    op.execute("DROP TABLE IF EXISTS approved_runtime_images")
    op.execute("DROP TABLE IF EXISTS tenant_entitlements")
    op.execute("DROP TABLE IF EXISTS tenant_runtime_quotas")
    op.execute("DROP TABLE IF EXISTS tenant_platform_settings")
    op.execute("DROP TABLE IF EXISTS plans")
    op.execute("DROP TABLE IF EXISTS platform_users")
