"""Redis Streams publisher for inference-log events.

The producer path only appends validated payloads onto a stream. Redaction and
Postgres writes belong to the consumer process.
"""

from __future__ import annotations

import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from chatlog.api.schemas import InferenceLogCreate
from chatlog.config import Settings, get_settings

logger = logging.getLogger(__name__)

STREAM_PAYLOAD_FIELD = "payload"


class StreamPublishError(Exception):
    """Raised when XADD fails after a Redis connection or command error."""


class InferenceLogStreamPublisher:
    """XADD validated inference events onto a Redis Stream."""

    def __init__(self, redis: Redis, settings: Settings | None = None) -> None:
        self._redis = redis
        self._settings = settings or get_settings()

    async def publish(self, payload: InferenceLogCreate) -> str:
        """Append ``payload`` to the inference stream and return the entry ID."""
        try:
            entry_id = await self._redis.xadd(
                self._settings.redis_stream,
                {STREAM_PAYLOAD_FIELD: payload.model_dump_json()},
            )
        except RedisError as exc:
            raise StreamPublishError(str(exc)) from exc
        if isinstance(entry_id, bytes):
            return entry_id.decode()
        return str(entry_id)


_redis_client: Redis | None = None


async def get_redis() -> Redis:
    """Return a process-wide async Redis client, creating it on first use."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


async def close_redis() -> None:
    """Close the shared Redis client if it was opened."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def publish_inference_log(payload: InferenceLogCreate) -> str:
    """Convenience wrapper used by HTTP and conversation producers."""
    redis = await get_redis()
    return await InferenceLogStreamPublisher(redis).publish(payload)


def parse_stream_payload(fields: dict[str, Any]) -> InferenceLogCreate:
    """Validate a stream entry's JSON payload field."""
    raw = fields.get(STREAM_PAYLOAD_FIELD)
    if raw is None:
        raise ValueError(f"stream entry missing '{STREAM_PAYLOAD_FIELD}' field")
    if isinstance(raw, bytes):
        raw = raw.decode()
    return InferenceLogCreate.model_validate_json(raw)
