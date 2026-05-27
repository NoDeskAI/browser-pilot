"""session file archives

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-14

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE session_files ADD COLUMN IF NOT EXISTS user_id TEXT")
    op.execute("ALTER TABLE session_files ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ")
    op.execute("ALTER TABLE session_files ADD COLUMN IF NOT EXISTS archived_session_id TEXT")
    op.execute("ALTER TABLE session_files ADD COLUMN IF NOT EXISTS archived_session_name TEXT")
    op.execute(
        """
        UPDATE session_files f
        SET user_id = s.user_id
        FROM sessions s
        WHERE f.session_id = s.id
          AND f.user_id IS NULL
        """
    )
    op.execute("ALTER TABLE session_files ALTER COLUMN session_id DROP NOT NULL")
    op.execute(
        """
        DO $$
        DECLARE fk_name TEXT;
        BEGIN
            SELECT c.conname INTO fk_name
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE c.conrelid = 'session_files'::regclass
              AND c.contype = 'f'
              AND a.attname = 'session_id'
            LIMIT 1;

            IF fk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE session_files DROP CONSTRAINT %I', fk_name);
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'session_files'::regclass
                  AND conname = 'session_files_session_id_fkey'
            ) THEN
                ALTER TABLE session_files
                ADD CONSTRAINT session_files_session_id_fkey
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_session_files_user_created ON session_files(user_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_session_files_archived_session ON session_files(archived_session_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_session_files_archived_session")
    op.execute("DROP INDEX IF EXISTS idx_session_files_user_created")
    op.execute("DELETE FROM session_files WHERE session_id IS NULL")
    op.execute(
        """
        DO $$
        DECLARE fk_name TEXT;
        BEGIN
            SELECT c.conname INTO fk_name
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            WHERE c.conrelid = 'session_files'::regclass
              AND c.contype = 'f'
              AND a.attname = 'session_id'
            LIMIT 1;

            IF fk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE session_files DROP CONSTRAINT %I', fk_name);
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE session_files ALTER COLUMN session_id SET NOT NULL")
    op.execute(
        """
        ALTER TABLE session_files
        ADD CONSTRAINT session_files_session_id_fkey
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
        """
    )
    op.execute("ALTER TABLE session_files DROP COLUMN IF EXISTS archived_session_name")
    op.execute("ALTER TABLE session_files DROP COLUMN IF EXISTS archived_session_id")
    op.execute("ALTER TABLE session_files DROP COLUMN IF EXISTS archived_at")
    op.execute("ALTER TABLE session_files DROP COLUMN IF EXISTS user_id")
