"""Create chat and analytics tables.

Revision ID: 0001
Revises:
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS chat")
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")

    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'cancelled')",
            name=op.f("ck_conversations_valid_status"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        schema="chat",
    )
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "sequence_number >= 0",
            name=op.f("ck_messages_nonnegative_sequence"),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name=op.f("ck_messages_valid_role"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat.conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_messages_conversation_id_sequence_number",
        ),
        schema="chat",
    )
    op.create_table(
        "inference_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_preview", sa.Text(), nullable=False),
        sa.Column("output_preview", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "input_tokens >= 0",
            name=op.f("ck_inference_logs_nonnegative_input_tokens"),
        ),
        sa.CheckConstraint(
            "latency_ms >= 0",
            name=op.f("ck_inference_logs_nonnegative_latency"),
        ),
        sa.CheckConstraint(
            "output_tokens >= 0",
            name=op.f("ck_inference_logs_nonnegative_output_tokens"),
        ),
        sa.CheckConstraint(
            "status IN ('success', 'error')",
            name=op.f("ck_inference_logs_valid_status"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat.conversations.id"],
            name="fk_inference_logs_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inference_logs"),
        schema="analytics",
    )
    # Minimal write-side indexing: conversation+time serves chat/log joins and
    # timeline reads; model+time serves the primary time-window stats access path.
    op.create_index(
        "ix_inference_logs_conversation_created",
        "inference_logs",
        ["conversation_id", "created_at"],
        schema="analytics",
    )
    op.create_index(
        "ix_inference_logs_model_created",
        "inference_logs",
        ["model", "created_at"],
        schema="analytics",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inference_logs_model_created",
        table_name="inference_logs",
        schema="analytics",
    )
    op.drop_index(
        "ix_inference_logs_conversation_created",
        table_name="inference_logs",
        schema="analytics",
    )
    op.drop_table("inference_logs", schema="analytics")
    op.drop_table("messages", schema="chat")
    op.drop_table("conversations", schema="chat")
    op.execute("DROP SCHEMA analytics")
    op.execute("DROP SCHEMA chat")
