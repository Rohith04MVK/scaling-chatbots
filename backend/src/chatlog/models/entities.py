import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chatlog.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'cancelled')", name="valid_status"),
        {"schema": "chat"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    title: Mapped[str | None] = mapped_column(String(255))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence_number",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="valid_role"),
        CheckConstraint("sequence_number >= 0", name="nonnegative_sequence"),
        UniqueConstraint("conversation_id", "sequence_number"),
        {"schema": "chat"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class InferenceLog(Base):
    __tablename__ = "inference_logs"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="nonnegative_latency"),
        CheckConstraint("input_tokens >= 0", name="nonnegative_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="nonnegative_output_tokens"),
        CheckConstraint("status IN ('success', 'error')", name="valid_status"),
        # These are the only secondary indexes: one supports per-conversation
        # timelines/joins, and one supports model-filtered time-window analytics.
        Index("ix_inference_logs_conversation_created", "conversation_id", "created_at"),
        Index("ix_inference_logs_model_created", "model", "created_at"),
        {"schema": "analytics"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    input_preview: Mapped[str] = mapped_column(Text, nullable=False)
    output_preview: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
