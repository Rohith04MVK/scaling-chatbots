"""Unit tests for consumer-side redact-then-write ingestion."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from chatlog.api.schemas import InferenceLogCreate
from chatlog.services.ingestion import LogIngestionService


async def test_ingestion_service_redacts_before_store_write() -> None:
    store = AsyncMock()
    store.write = AsyncMock(return_value=uuid.uuid4())
    payload = InferenceLogCreate(
        model="gpt-4.1-mini",
        provider="openai",
        conversation_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        latency_ms=10,
        input_tokens=1,
        output_tokens=1,
        status="success",
        error_message=None,
        input_preview="Hello from person@example.com",
        output_preview="Call 415-555-2671",
        timestamp=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )

    await LogIngestionService(store).ingest(payload)

    written = store.write.await_args.args[0]
    assert written.input_preview == "Hello from <EMAIL_ADDRESS>"
    assert written.output_preview == "Call <PHONE_NUMBER>"
