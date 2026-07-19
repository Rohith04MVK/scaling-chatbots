"""Send a mocked OpenAI-shaped call to a running local ingestion backend."""

import os
import time
from typing import Any

import httpx
from chatlog_sdk import InstrumentationClient, conversation_context

backend_url = os.getenv("LLM_LOG_BACKEND_URL", "http://localhost:8000")
client = InstrumentationClient(backend_url=backend_url)


@client.instrument(provider="openai")
def mocked_openai_call(prompt: str) -> dict[str, Any]:
    time.sleep(0.05)
    return {
        "model": "gpt-4.1-mini",
        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        "choices": [{"message": {"content": f"Mock answer for: {prompt}"}}],
    }


def main() -> None:
    conversations = httpx.get(f"{backend_url}/conversations", timeout=2).json()
    if not conversations:
        raise SystemExit("No conversations found. Run the backend seed script first.")

    conversation_id = conversations[0]["id"]
    with conversation_context(conversation_id):
        response = mocked_openai_call("Contact me at demo@example.com")

    print(response["choices"][0]["message"]["content"])
    if client.flush(timeout=2):
        print(f"Inference log delivered for conversation {conversation_id}")
    else:
        print("The response succeeded; telemetry is still queued or was dropped.")
    client.close()


if __name__ == "__main__":
    main()
