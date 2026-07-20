import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from chatlog.main import app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.fixture
def valid_payload() -> dict[str, Any]:
    return {
        "model": "gpt-4.1-mini",
        "provider": "openai",
        "conversation_id": "11111111-1111-1111-1111-111111111111",
        "latency_ms": 350,
        "input_tokens": 25,
        "output_tokens": 50,
        "status": "success",
        "error_message": None,
        "input_preview": "Hello from person@example.com",
        "output_preview": "Call 415-555-2671",
        "timestamp": "2026-07-18T12:00:00Z",
    }


@pytest.mark.parametrize(
    "change",
    [
        {"latency_ms": -1},
        {"latency_ms": "350"},
        {"status": "unknown"},
        {"conversation_id": "not-a-uuid"},
        {"timestamp": "2026-07-18T12:00:00"},
        {"status": "error", "error_message": None},
        {"status": "success", "error_message": "unexpected"},
    ],
)
async def test_ingest_rejects_malformed_payloads(
    client: AsyncClient,
    valid_payload: dict[str, Any],
    change: dict[str, Any],
) -> None:
    with patch(
        "chatlog.api.logs.publish_inference_log",
        new=AsyncMock(),
    ) as publish:
        response = await client.post("/logs/ingest", json=valid_payload | change)

    assert response.status_code == 422
    assert response.json()["detail"]
    publish.assert_not_awaited()


async def test_ingest_rejects_missing_required_field(
    client: AsyncClient,
    valid_payload: dict[str, Any],
) -> None:
    valid_payload.pop("model")

    with patch(
        "chatlog.api.logs.publish_inference_log",
        new=AsyncMock(),
    ) as publish:
        response = await client.post("/logs/ingest", json=valid_payload)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "model"]
    publish.assert_not_awaited()


async def test_ingest_enqueues_validated_payload_without_redacting(
    client: AsyncClient,
    valid_payload: dict[str, Any],
) -> None:
    # Edge validation only — redaction is the consumer's job.
    with patch(
        "chatlog.api.logs.publish_inference_log",
        new=AsyncMock(return_value="1710000000000-0"),
    ) as publish:
        response = await client.post("/logs/ingest", json=valid_payload)

    assert response.status_code == 202
    assert response.json() == {
        "accepted": True,
        "stream_id": "1710000000000-0",
        "warning": None,
    }
    published = publish.await_args.args[0]
    assert published.input_preview == "Hello from person@example.com"
    assert published.output_preview == "Call 415-555-2671"
    assert published.conversation_id == uuid.UUID("11111111-1111-1111-1111-111111111111")


async def test_ingest_returns_202_with_warning_when_redis_unreachable(
    client: AsyncClient,
    valid_payload: dict[str, Any],
) -> None:
    from chatlog.services.stream import StreamPublishError

    with patch(
        "chatlog.api.logs.publish_inference_log",
        new=AsyncMock(side_effect=StreamPublishError("connection refused")),
    ):
        response = await client.post("/logs/ingest", json=valid_payload)

    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["stream_id"] is None
    assert body["warning"] is not None
    assert "redis_unavailable" in body["warning"]
