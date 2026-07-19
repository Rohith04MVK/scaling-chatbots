from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from chatlog.api.conversations import _complete_conversation, _stream_conversation
from chatlog.main import app
from chatlog.models import Conversation, Message
from chatlog.services.llm import Completion


def test_dashboard_endpoint_exposes_minute_window_contract() -> None:
    operation = app.openapi()["paths"]["/dashboard"]["get"]
    parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}

    assert parameters["window_minutes"]["schema"]["minimum"] == 1
    assert operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/DashboardResponse")


async def test_assistant_completion_is_committed_after_logging() -> None:
    conversation = Conversation(
        provider="groq",
        model="llama-3.3-70b-versatile",
        messages=[
            Message(
                role="user",
                content="Hello",
                sequence_number=0,
                created_at=datetime.now(UTC),
            )
        ],
    )
    session = AsyncMock()
    completion = Completion(content="Hi", input_tokens=1, output_tokens=1)

    with (
        patch(
            "chatlog.api.conversations.LLMClient.complete",
            new=AsyncMock(return_value=completion),
        ),
        patch("chatlog.api.conversations._write_inference_log", new=AsyncMock()),
    ):
        await _complete_conversation(conversation, session)

    assert conversation.messages[-1].role == "assistant"
    session.commit.assert_awaited_once()


async def test_streamed_completion_is_persisted_after_done_event() -> None:
    conversation = Conversation(
        provider="groq",
        model="llama-3.3-70b-versatile",
        messages=[
            Message(
                role="user",
                content="Hello",
                sequence_number=0,
                created_at=datetime.now(UTC),
            )
        ],
    )
    session = AsyncMock()

    async def stream(*_args: object, **_kwargs: object):
        yield "Hi", 1, 1
        yield " there", 1, 2

    with (
        patch("chatlog.api.conversations.LLMClient.stream", new=stream),
        patch("chatlog.api.conversations._write_inference_log", new=AsyncMock()),
    ):
        events = [event async for event in _stream_conversation(conversation, session)]

    assert events == [
        'event: delta\ndata: {"content": "Hi"}\n\n',
        'event: delta\ndata: {"content": " there"}\n\n',
        "event: done\ndata: {}\n\n",
    ]
    assert conversation.messages[-1].content == "Hi there"
    session.commit.assert_awaited_once()
