import uuid
from typing import Protocol

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chatlog.api.schemas import InferenceLogCreate
from chatlog.models import InferenceLog


class ConversationNotFoundError(Exception):
    pass


class LogStore(Protocol):
    async def write(self, payload: InferenceLogCreate) -> uuid.UUID: ...


class PostgresLogStore:
    """Append inference logs to Postgres through a small swappable boundary."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def write(self, payload: InferenceLogCreate) -> uuid.UUID:
        log = InferenceLog(
            model=payload.model,
            provider=payload.provider,
            conversation_id=payload.conversation_id,
            latency_ms=payload.latency_ms,
            input_tokens=payload.input_tokens,
            output_tokens=payload.output_tokens,
            status=payload.status,
            error_message=payload.error_message,
            input_preview=payload.input_preview,
            output_preview=payload.output_preview,
            created_at=payload.timestamp,
        )
        self._session.add(log)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConversationNotFoundError from exc
        return log.id
