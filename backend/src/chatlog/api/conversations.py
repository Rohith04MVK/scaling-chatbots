import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from chatlog.api.dependencies import SessionDependency
from chatlog.api.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationResponse,
    InferenceLogCreate,
    MessageCreate,
)
from chatlog.config import get_settings
from chatlog.models import Conversation, Message
from chatlog.providers import default_provider_id, get_provider, resolve_model
from chatlog.services.llm import (
    ChatMessage,
    Completion,
    LLMClient,
    LLMConfigurationError,
    LLMProviderError,
)
from chatlog.services.redaction import redact
from chatlog.services.stream import StreamPublishError, publish_inference_log

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _sse(event: str, payload: dict[str, object]) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def _conversation_payload(conversation: Conversation) -> dict[str, object]:
    return {
        "id": str(conversation.id),
        "created_at": conversation.created_at.isoformat(),
        "status": conversation.status,
        "title": conversation.title,
        "provider": conversation.provider,
        "model": conversation.model,
    }


async def _load_conversation(
    conversation_id: uuid.UUID,
    session: SessionDependency,
) -> Conversation | None:
    statement = (
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(selectinload(Conversation.messages))
    )
    return await session.scalar(statement)


def _resolve_provider_selection(
    *,
    provider: str | None,
    model: str | None,
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
) -> tuple[str, str]:
    settings = get_settings()
    provider_id = provider or fallback_provider or default_provider_id(settings)
    try:
        spec = get_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    resolved_model = resolve_model(spec, model or fallback_model)
    return provider_id, resolved_model


async def _complete_conversation(
    conversation: Conversation,
    session: SessionDependency,
    *,
    api_key: str | None = None,
) -> None:
    if not conversation.provider or not conversation.model:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Conversation is missing provider/model selection",
        )

    settings = get_settings()
    llm = LLMClient(settings)
    messages: list[ChatMessage] = [
        {"role": message.role, "content": message.content}  # type: ignore[typeddict-item]
        for message in conversation.messages
    ]
    started_at = perf_counter()
    try:
        completion = await llm.complete(
            messages,
            provider=conversation.provider,
            model=conversation.model,
            api_key=api_key,
        )
    except (LLMConfigurationError, LLMProviderError) as exc:
        latency_ms = round((perf_counter() - started_at) * 1000)
        await _write_inference_log(
            conversation=conversation,
            completion=None,
            latency_ms=latency_ms,
            error=exc,
        )
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if isinstance(exc, LLMConfigurationError)
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    conversation.messages.append(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=redact(completion.content),
            sequence_number=len(conversation.messages),
        )
    )
    await _write_inference_log(
        conversation=conversation,
        completion=completion,
        latency_ms=round((perf_counter() - started_at) * 1000),
        error=None,
    )
    # Keep the assistant message durable before the API reloads the conversation.
    # Inference logs are enqueued to Redis separately and do not share this commit.
    await session.commit()


async def _stream_conversation(
    conversation: Conversation,
    session: SessionDependency,
    *,
    api_key: str | None = None,
) -> AsyncIterator[str]:
    if not conversation.provider or not conversation.model:
        yield _sse("error", {"detail": "Conversation is missing provider/model selection"})
        return

    llm = LLMClient(get_settings())
    messages: list[ChatMessage] = [
        {"role": message.role, "content": message.content}  # type: ignore[typeddict-item]
        for message in conversation.messages
    ]
    started_at = perf_counter()
    content_parts: list[str] = []
    input_tokens = 0
    output_tokens = 0
    try:
        async for delta, latest_input_tokens, latest_output_tokens in llm.stream(
            messages,
            provider=conversation.provider,
            model=conversation.model,
            api_key=api_key,
        ):
            input_tokens = latest_input_tokens
            output_tokens = latest_output_tokens
            if delta:
                content_parts.append(delta)
                yield _sse("delta", {"content": delta})
    except (LLMConfigurationError, LLMProviderError) as exc:
        await _write_inference_log(
            conversation=conversation,
            completion=None,
            latency_ms=round((perf_counter() - started_at) * 1000),
            error=exc,
        )
        yield _sse("error", {"detail": str(exc)})
        return

    content = "".join(content_parts)
    if not content.strip():
        error = LLMProviderError("LLM provider returned an empty assistant message")
        await _write_inference_log(
            conversation=conversation,
            completion=None,
            latency_ms=round((perf_counter() - started_at) * 1000),
            error=error,
        )
        yield _sse("error", {"detail": str(error)})
        return

    completion = Completion(
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    conversation.messages.append(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=redact(content),
            sequence_number=len(conversation.messages),
        )
    )
    await _write_inference_log(
        conversation=conversation,
        completion=completion,
        latency_ms=round((perf_counter() - started_at) * 1000),
        error=None,
    )
    await session.commit()
    yield _sse("done", {})


async def _write_inference_log(
    *,
    conversation: Conversation,
    completion: Completion | None,
    latency_ms: int,
    error: Exception | None,
) -> None:
    # Same producer path as POST /logs/ingest: validate + XADD. The consumer
    # owns redaction and the Postgres write.
    latest_user_message = next(
        (message.content for message in reversed(conversation.messages) if message.role == "user"),
        "",
    )
    payload = InferenceLogCreate(
        model=conversation.model or "unknown",
        provider=conversation.provider or "unknown",
        conversation_id=conversation.id,
        latency_ms=max(0, latency_ms),
        input_tokens=completion.input_tokens if completion else 0,
        output_tokens=completion.output_tokens if completion else 0,
        status="error" if error else "success",
        error_message=str(error)[:4000] if error else None,
        input_preview=latest_user_message[:8000],
        output_preview=completion.content[:8000] if completion else "",
        timestamp=datetime.now(UTC),
    )
    try:
        await publish_inference_log(payload)
    except StreamPublishError:
        # Match SDK fire-and-forget: a logging outage must not fail the chat turn.
        logger.exception("Redis unreachable while enqueueing conversation inference log")


@router.post("", response_model=ConversationDetail, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    session: SessionDependency,
) -> Conversation:
    provider_id, model = _resolve_provider_selection(
        provider=payload.provider,
        model=payload.model,
    )
    conversation = Conversation(
        title=payload.title or payload.message.splitlines()[0][:80],
        provider=provider_id,
        model=model,
        messages=[
            Message(
                role="user",
                content=redact(payload.message),
                sequence_number=0,
            )
        ],
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation, attribute_names=["messages"])
    await _complete_conversation(conversation, session, api_key=payload.api_key)
    refreshed = await _load_conversation(conversation.id, session)
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return refreshed


@router.post("/stream", status_code=status.HTTP_201_CREATED)
async def create_conversation_stream(
    payload: ConversationCreate,
    session: SessionDependency,
) -> StreamingResponse:
    provider_id, model = _resolve_provider_selection(
        provider=payload.provider,
        model=payload.model,
    )
    conversation = Conversation(
        title=payload.title or payload.message.splitlines()[0][:80],
        provider=provider_id,
        model=model,
        messages=[Message(role="user", content=redact(payload.message), sequence_number=0)],
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation, attribute_names=["messages"])

    async def events() -> AsyncIterator[str]:
        yield _sse("conversation", _conversation_payload(conversation))
        async for event in _stream_conversation(conversation, session, api_key=payload.api_key):
            yield event

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    session: SessionDependency,
    conversation_status: Literal["active", "cancelled"] | None = Query(
        default=None, alias="status"
    ),
) -> list[Conversation]:
    statement = select(Conversation).order_by(Conversation.created_at.desc())
    if conversation_status is not None:
        statement = statement.where(Conversation.status == conversation_status)
    return list((await session.scalars(statement)).all())


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    session: SessionDependency,
) -> Conversation:
    conversation = await _load_conversation(conversation_id, session)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@router.post("/{conversation_id}/messages", response_model=ConversationDetail)
async def append_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    session: SessionDependency,
) -> Conversation:
    conversation = await _load_conversation(conversation_id, session)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if conversation.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancelled conversations cannot accept new messages",
        )

    provider_id, model = _resolve_provider_selection(
        provider=payload.provider,
        model=payload.model,
        fallback_provider=conversation.provider,
        fallback_model=conversation.model,
    )
    conversation.provider = provider_id
    conversation.model = model
    conversation.messages.append(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=redact(payload.message),
            sequence_number=len(conversation.messages),
        )
    )
    await session.commit()
    await _complete_conversation(conversation, session, api_key=payload.api_key)
    refreshed = await _load_conversation(conversation.id, session)
    if refreshed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return refreshed


@router.post("/{conversation_id}/messages/stream")
async def append_message_stream(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    session: SessionDependency,
) -> StreamingResponse:
    conversation = await _load_conversation(conversation_id, session)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if conversation.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cancelled conversations cannot accept new messages",
        )

    provider_id, model = _resolve_provider_selection(
        provider=payload.provider,
        model=payload.model,
        fallback_provider=conversation.provider,
        fallback_model=conversation.model,
    )
    conversation.provider = provider_id
    conversation.model = model
    conversation.messages.append(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=redact(payload.message),
            sequence_number=len(conversation.messages),
        )
    )
    await session.commit()

    async def events() -> AsyncIterator[str]:
        yield _sse("conversation", _conversation_payload(conversation))
        async for event in _stream_conversation(conversation, session, api_key=payload.api_key):
            yield event

    return StreamingResponse(events(), media_type="text/event-stream")


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    session: SessionDependency,
) -> None:
    conversation = await _load_conversation(conversation_id, session)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    await session.delete(conversation)
    await session.commit()


@router.post("/{conversation_id}/cancel", response_model=ConversationResponse)
async def cancel_conversation(
    conversation_id: uuid.UUID,
    session: SessionDependency,
) -> Conversation:
    statement = (
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(status="cancelled")
        .returning(Conversation)
    )
    conversation = (await session.scalars(statement)).one_or_none()
    if conversation is None:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    await session.commit()
    return conversation
