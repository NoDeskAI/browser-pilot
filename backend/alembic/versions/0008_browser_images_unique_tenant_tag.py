"""add unique constraint (tenant_id, image_tag) on browser_images

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-23

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE browser_images "
        "ADD CONSTRAINT uq_browser_images_tenant_tag UNIQUE (tenant_id, image_tag)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE browser_images DROP CONSTRAINT IF EXISTS uq_browser_images_tenant_tag"
    )
