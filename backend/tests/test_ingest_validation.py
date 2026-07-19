import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from chatlog.api.dependencies import get_log_store
from chatlog.api.schemas import InferenceLogCreate
from chatlog.main import app
from httpx import ASGITransport, AsyncClient


class RecordingLogStore:
    def __init__(self) -> None:
        self.payloads: list[InferenceLogCreate] = []

    async def write(self, payload: InferenceLogCreate) -> uuid.UUID:
        self.payloads.append(payload)
        return uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture
def store() -> RecordingLogStore:
    return RecordingLogStore()


@pytest.fixture
async def client(store: RecordingLogStore) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_log_store] = lambda: store
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


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
    store: RecordingLogStore,
    valid_payload: dict[str, Any],
    change: dict[str, Any],
) -> None:
    response = await client.post("/logs/ingest", json=valid_payload | change)

    assert response.status_code == 422
    assert response.json()["detail"]
    assert store.payloads == []


async def test_ingest_rejects_missing_required_field(
    client: AsyncClient,
    store: RecordingLogStore,
    valid_payload: dict[str, Any],
) -> None:
    valid_payload.pop("model")

    response = await client.post("/logs/ingest", json=valid_payload)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "model"]
    assert store.payloads == []


async def test_ingest_redacts_previews_before_writing(
    client: AsyncClient,
    store: RecordingLogStore,
    valid_payload: dict[str, Any],
) -> None:
    response = await client.post("/logs/ingest", json=valid_payload)

    assert response.status_code == 201
    assert response.json() == {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "accepted": True,
    }
    assert store.payloads[0].input_preview == "Hello from <EMAIL_ADDRESS>"
    assert store.payloads[0].output_preview == "Call <PHONE_NUMBER>"
