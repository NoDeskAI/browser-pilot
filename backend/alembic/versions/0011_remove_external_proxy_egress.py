"""remove external proxy egress

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-07

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    UPDATE sessions
    SET proxy_url = ''
    WHERE network_egress_id IS NULL AND COALESCE(proxy_url, '') <> ''
    """)
    op.execute("""
    UPDATE sessions s
    SET proxy_url = ''
    FROM network_egress_profiles e
    WHERE s.network_egress_id = e.id
      AND e.type = 'external_proxy'
    """)
    op.execute("DELETE FROM network_egress_profiles WHERE type = 'external_proxy'")
    op.execute("ALTER TABLE network_egress_profiles DROP CONSTRAINT IF EXISTS network_egress_profiles_type_check")
    op.execute("""
    ALTER TABLE network_egress_profiles
      ADD CONSTRAINT network_egress_profiles_type_check
      CHECK (type IN ('clash', 'openvpn'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE network_egress_profiles DROP CONSTRAINT IF EXISTS network_egress_profiles_type_check")
    op.execute("""
    ALTER TABLE network_egress_profiles
      ADD CONSTRAINT network_egress_profiles_type_check
      CHECK (type IN ('direct', 'external_proxy', 'clash', 'openvpn'))
    """)
