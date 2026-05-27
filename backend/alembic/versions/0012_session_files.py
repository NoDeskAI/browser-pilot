"""session file records

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-12

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS session_files (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        tenant_id TEXT,
        source TEXT NOT NULL,
        original_name TEXT NOT NULL,
        content_type TEXT NOT NULL,
        size_bytes BIGINT NOT NULL,
        storage TEXT NOT NULL,
        object_key TEXT NOT NULL,
        source_path TEXT,
        source_mtime DOUBLE PRECISION,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_session_files_session_created ON session_files(session_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_session_files_tenant ON session_files(tenant_id)")
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_session_files_download_dedupe
    ON session_files(session_id, source, source_path, source_mtime, size_bytes)
    WHERE source_path IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_session_files_download_dedupe")
    op.execute("DROP INDEX IF EXISTS idx_session_files_tenant")
    op.execute("DROP INDEX IF EXISTS idx_session_files_session_created")
    op.execute("DROP TABLE IF EXISTS session_files")
