"""Workspace invitation expiry

Revision ID: 023
Revises: 022
Create Date: 2026-08-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invitations",
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_invitations_expires_at", "invitations", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_invitations_expires_at", table_name="invitations")
    op.drop_column("invitations", "expires_at")
