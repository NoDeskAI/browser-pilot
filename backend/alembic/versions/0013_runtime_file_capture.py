"""runtime file capture ingest

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-13

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE session_files ADD COLUMN IF NOT EXISTS source_id TEXT")
    op.execute("ALTER TABLE session_files ADD COLUMN IF NOT EXISTS sha256 TEXT")
    op.execute("ALTER TABLE session_files ADD COLUMN IF NOT EXISTS uploaded_at TIMESTAMPTZ")
    op.execute("UPDATE session_files SET uploaded_at = created_at WHERE uploaded_at IS NULL")
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_files_source_id_dedupe
    ON session_files(session_id, source, source_id)
    WHERE source_id IS NOT NULL AND source_id <> ''
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS session_runtime_tokens (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        tenant_id TEXT,
        purpose TEXT NOT NULL,
        token_hash TEXT UNIQUE NOT NULL,
        expires_at TIMESTAMPTZ,
        revoked_at TIMESTAMPTZ,
        last_used_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_session_runtime_tokens_session ON session_runtime_tokens(session_id, purpose)")
    op.execute("""
    CREATE TABLE IF NOT EXISTS session_runtime_status (
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL,
        status TEXT NOT NULL,
        last_heartbeat_at TIMESTAMPTZ,
        last_error TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (session_id, purpose)
    )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_runtime_status")
    op.execute("DROP INDEX IF EXISTS idx_session_runtime_tokens_session")
    op.execute("DROP TABLE IF EXISTS session_runtime_tokens")
    op.execute("DROP INDEX IF EXISTS idx_session_files_source_id_dedupe")
    op.execute("ALTER TABLE session_files DROP COLUMN IF EXISTS uploaded_at")
    op.execute("ALTER TABLE session_files DROP COLUMN IF EXISTS sha256")
    op.execute("ALTER TABLE session_files DROP COLUMN IF EXISTS source_id")
