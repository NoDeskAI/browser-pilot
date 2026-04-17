"""initial schema

Revision ID: 0001
Revises:
Create Date: 2025-04-17

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        current_url TEXT,
        current_title TEXT
    )
    """)

    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS current_url TEXT")
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS current_title TEXT")
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS device_preset TEXT DEFAULT 'desktop-1280x800'")
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS proxy_url TEXT DEFAULT ''")

    op.execute("""
    CREATE TABLE IF NOT EXISTS app_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS tenants (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL REFERENCES tenants(id),
        email TEXT NOT NULL,
        password_hash TEXT,
        name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'member',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(tenant_id, email)
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS api_tokens (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id),
        tenant_id TEXT NOT NULL REFERENCES tenants(id),
        name TEXT NOT NULL,
        token_hash TEXT UNIQUE NOT NULL,
        last_used_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)

    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS tenant_id TEXT")
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_id TEXT")


def downgrade() -> None:
    op.drop_table("api_tokens")
    op.drop_table("users")
    op.drop_table("sessions")
    op.drop_table("tenants")
    op.drop_table("app_state")
