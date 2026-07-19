import asyncio
from datetime import UTC, datetime, timedelta

from chatlog.api.schemas import InferenceLogCreate
from chatlog.db.session import async_session_factory, engine
from chatlog.models import Conversation, Message
from chatlog.services.ingestion import LogIngestionService
from chatlog.services.log_store import PostgresLogStore


async def seed() -> None:
    now = datetime.now(UTC)
    conversations = [
        Conversation(title="Plan a weekend trip"),
        Conversation(title="Debug an async worker"),
        Conversation(title="Summarize a report", status="cancelled"),
    ]
    async with async_session_factory() as session:
        session.add_all(conversations)
        await session.flush()
        session.add_all(
            [
                Message(
                    conversation_id=conversations[0].id,
                    role="user",
                    content="Suggest a quiet weekend destination.",
                    sequence_number=0,
                ),
                Message(
                    conversation_id=conversations[0].id,
                    role="assistant",
                    content="Consider a cabin near a state park.",
                    sequence_number=1,
                ),
                Message(
                    conversation_id=conversations[1].id,
                    role="user",
                    content="Why is my worker timing out?",
                    sequence_number=0,
                ),
            ]
        )
        await session.commit()

        service = LogIngestionService(PostgresLogStore(session))
        demo_logs = [
            InferenceLogCreate(
                model="gpt-4.1-mini",
                provider="openai",
                conversation_id=conversations[0].id,
                latency_ms=420,
                input_tokens=85,
                output_tokens=146,
                status="success",
                input_preview="Trip request from demo@example.com",
                output_preview="A short list of destinations",
                timestamp=now - timedelta(minutes=20),
            ),
            InferenceLogCreate(
                model="claude-sonnet-4",
                provider="anthropic",
                conversation_id=conversations[1].id,
                latency_ms=690,
                input_tokens=124,
                output_tokens=210,
                status="success",
                input_preview="Debug this worker",
                output_preview="Potential timeout causes",
                timestamp=now - timedelta(minutes=10),
            ),
            InferenceLogCreate(
                model="gpt-4.1-mini",
                provider="openai",
                conversation_id=conversations[2].id,
                latency_ms=1100,
                input_tokens=200,
                output_tokens=0,
                status="error",
                error_message="Provider timeout",
                input_preview="Summarize the report",
                output_preview="",
                timestamp=now - timedelta(minutes=5),
            ),
        ]
        for payload in demo_logs:
            await service.ingest(payload)

    await engine.dispose()
    print("Inserted 3 conversations, 3 messages, and 3 inference logs.")


if __name__ == "__main__":
    asyncio.run(seed())
