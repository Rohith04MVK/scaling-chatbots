"""Unit tests for Redis Streams consumer DLQ / ack behavior."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from chatlog.config import Settings
from chatlog.services.stream_consumer import InferenceLogConsumer


@pytest.fixture
def settings() -> Settings:
    return Settings(
        redis_stream="inference_logs",
        redis_dlq_stream="inference_logs_dlq",
        redis_consumer_group="inference_logs_writers",
        redis_consumer_name="test-consumer",
        redis_max_delivery_attempts=2,
    )


def _consumer(settings: Settings) -> tuple[InferenceLogConsumer, AsyncMock]:
    redis = AsyncMock()
    return InferenceLogConsumer(redis, settings), redis


async def test_successful_write_acks_message(settings: Settings) -> None:
    consumer, redis = _consumer(settings)
    payload = (
        '{"model":"gpt-4.1-mini","provider":"openai",'
        '"conversation_id":"11111111-1111-1111-1111-111111111111",'
        '"latency_ms":1,"input_tokens":1,"output_tokens":1,"status":"success",'
        '"error_message":null,"input_preview":"hi","output_preview":"yo",'
        '"timestamp":"2026-07-18T12:00:00Z"}'
    )
    fields = {"payload": payload}

    with patch("chatlog.services.stream_consumer.LogIngestionService") as ingestion_cls:
        service = MagicMock()
        service.ingest = AsyncMock()
        ingestion_cls.return_value = service
        with patch("chatlog.services.stream_consumer.async_session_factory") as session_factory:
            session = AsyncMock()
            session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
            session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
            await consumer._handle_entry("1-0", fields, delivery_count=1)

    redis.xack.assert_awaited_once_with(
        "inference_logs",
        "inference_logs_writers",
        "1-0",
    )
    redis.xadd.assert_not_awaited()


async def test_failed_write_below_max_attempts_leaves_unacked(settings: Settings) -> None:
    consumer, redis = _consumer(settings)
    fields = {"payload": "not-json"}

    await consumer._handle_entry("2-0", fields, delivery_count=1)

    redis.xack.assert_not_awaited()
    redis.xadd.assert_not_awaited()


async def test_failed_write_at_max_attempts_moves_to_dlq(settings: Settings) -> None:
    consumer, redis = _consumer(settings)
    fields: dict[str, Any] = {"payload": "not-json"}

    await consumer._handle_entry("3-0", fields, delivery_count=2)

    redis.xadd.assert_awaited_once()
    dlq_args = redis.xadd.await_args
    assert dlq_args.args[0] == "inference_logs_dlq"
    assert dlq_args.args[1]["source_id"] == "3-0"
    assert dlq_args.args[1]["delivery_count"] == "2"
    redis.xack.assert_awaited_once_with(
        "inference_logs",
        "inference_logs_writers",
        "3-0",
    )
