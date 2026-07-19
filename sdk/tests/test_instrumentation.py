import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from chatlog_sdk import InstrumentationClient, conversation_context, get_conversation_id


class RecordingSender:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.attempts = 0
        self.payloads: list[Mapping[str, Any]] = []
        self.closed = False

    def send(self, payload: Mapping[str, Any]) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OSError("backend unavailable")
        self.payloads.append(payload)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def conversation_id() -> uuid.UUID:
    return uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_conversation_context_is_nested_and_restored(conversation_id: uuid.UUID) -> None:
    second_id = uuid.UUID("22222222-2222-2222-2222-222222222222")

    assert get_conversation_id() is None
    with conversation_context(conversation_id):
        assert get_conversation_id() == str(conversation_id)
        with conversation_context(second_id):
            assert get_conversation_id() == str(second_id)
        assert get_conversation_id() == str(conversation_id)
    assert get_conversation_id() is None


def test_decorator_extracts_metadata_and_redacts_previews(
    conversation_id: uuid.UUID,
) -> None:
    sender = RecordingSender(failures=1)
    client = InstrumentationClient(sender=sender, preview_chars=500)

    @client.instrument(provider="openai")
    def complete(prompt: str) -> dict[str, Any]:
        return {
            "model": "gpt-4.1-mini",
            "usage": {"prompt_tokens": 8, "completion_tokens": 4},
            "choices": [{"message": {"content": "Call 415-555-2671"}}],
        }

    with conversation_context(conversation_id):
        response = complete("Email person@example.com")

    assert response["model"] == "gpt-4.1-mini"
    assert client.flush()
    assert sender.attempts == 2
    payload = sender.payloads[0]
    assert payload["conversation_id"] == str(conversation_id)
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-4.1-mini"
    assert payload["input_tokens"] == 8
    assert payload["output_tokens"] == 4
    assert payload["status"] == "success"
    assert payload["input_preview"] == "Email <EMAIL_ADDRESS>"
    assert payload["output_preview"] == "Call <PHONE_NUMBER>"
    client.close()


@pytest.mark.asyncio
async def test_async_decorator_reraises_original_exception(
    conversation_id: uuid.UUID,
) -> None:
    sender = RecordingSender()
    client = InstrumentationClient(sender=sender)

    @client.instrument(provider="anthropic", model="claude-sonnet-4")
    async def fail() -> None:
        raise RuntimeError("provider failed for user@example.com")

    with conversation_context(conversation_id):
        with pytest.raises(RuntimeError, match="provider failed"):
            await fail()

    assert client.flush()
    payload = sender.payloads[0]
    assert payload["status"] == "error"
    # Error messages are not part of the preview redaction pipeline.
    assert payload["error_message"] == "RuntimeError: provider failed for user@example.com"
    assert payload["input_tokens"] == 0
    client.close()


def test_context_manager_records_assigned_response(conversation_id: uuid.UUID) -> None:
    sender = RecordingSender()
    client = InstrumentationClient(sender=sender)
    response = {
        "model": "claude-sonnet-4",
        "usage": {"input_tokens": 5, "output_tokens": 3},
        "content": [{"text": "Done"}],
    }

    with conversation_context(conversation_id):
        with client.instrumented_call(
            provider="anthropic",
            input_data="Summarize this",
        ) as call:
            returned = call.set_response(response)

    assert returned is response
    assert client.flush()
    payload = sender.payloads[0]
    assert payload["provider"] == "anthropic"
    assert payload["output_preview"] == "Done"
    assert payload["input_tokens"] == 5
    client.close()


def test_missing_conversation_context_drops_only_telemetry() -> None:
    sender = RecordingSender()
    client = InstrumentationClient(sender=sender)

    @client.instrument(provider="openai", model="gpt-4.1-mini")
    def complete() -> dict[str, Any]:
        return {
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "choices": [{"message": {"content": "ok"}}],
        }

    assert complete()["choices"][0]["message"]["content"] == "ok"
    assert client.flush()
    assert sender.payloads == []
    client.close()


def test_backend_downtime_never_breaks_wrapped_call(conversation_id: uuid.UUID) -> None:
    sender = RecordingSender(failures=99)
    client = InstrumentationClient(sender=sender)

    @client.instrument(provider="openai", model="gpt-4.1-mini")
    def complete() -> dict[str, Any]:
        return {
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "choices": [{"message": {"content": "still returned"}}],
        }

    with conversation_context(conversation_id):
        response = complete()

    assert response["choices"][0]["message"]["content"] == "still returned"
    assert client.flush()
    assert sender.attempts == 2
    assert sender.payloads == []
    client.close()
