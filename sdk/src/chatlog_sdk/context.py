import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_conversation_id: ContextVar[str | None] = ContextVar("chatlog_conversation_id", default=None)


def get_conversation_id() -> str | None:
    """Return the conversation ID active in the current execution context."""
    return _conversation_id.get()


@contextmanager
def conversation_context(conversation_id: str | uuid.UUID) -> Iterator[None]:
    """Propagate a conversation ID through nested sync and async LLM calls."""
    normalized = str(uuid.UUID(str(conversation_id)))
    token = _conversation_id.set(normalized)
    try:
        yield
    finally:
        _conversation_id.reset(token)
