"""Add provider and model columns to conversations.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("provider", sa.String(length=64), nullable=True),
        schema="chat",
    )
    op.add_column(
        "conversations",
        sa.Column("model", sa.String(length=128), nullable=True),
        schema="chat",
    )


def downgrade() -> None:
    op.drop_column("conversations", "model", schema="chat")
    op.drop_column("conversations", "provider", schema="chat")
