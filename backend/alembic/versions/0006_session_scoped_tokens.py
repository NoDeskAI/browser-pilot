"""session-scoped API tokens

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-20

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE api_tokens ADD COLUMN session_id TEXT")
    op.execute(
        "ALTER TABLE api_tokens ADD CONSTRAINT fk_api_tokens_session_id "
        "FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE api_tokens DROP CONSTRAINT IF EXISTS fk_api_tokens_session_id")
    op.execute("ALTER TABLE api_tokens DROP COLUMN IF EXISTS session_id")
