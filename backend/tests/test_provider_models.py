from unittest.mock import AsyncMock, patch

import httpx
import pytest
from chatlog.api.providers import router
from chatlog.config import Settings
from chatlog.providers import (
    PROVIDERS,
    ProviderModelsError,
    _is_chat_model,
    fetch_provider_models,
    pick_default_model,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_pick_default_model_prefers_configured_default() -> None:
    assert pick_default_model(["a", "b", "c"], "b") == "b"
    assert pick_default_model(["a", "b"], "missing") == "a"
    assert pick_default_model([], "fallback") == "fallback"


@pytest.mark.parametrize(
    ("provider_id", "model_id", "expected"),
    [
        ("groq", "llama-3.3-70b-versatile", True),
        ("groq", "whisper-large-v3", False),
        ("groq", "playai-tts", False),
        ("groq", "meta-llama/llama-guard-4-12b", False),
        ("openai", "gpt-4.1-mini", True),
        ("openai", "o3-mini", True),
        ("openai", "gpt-4o-audio-preview", False),
        ("openai", "tts-1-hd", False),
        ("openai", "dall-e-3", False),
        ("openai", "text-embedding-3-small", False),
        ("openai", "gpt-image-1", False),
        ("gemini", "gemini-2.0-flash", True),
        ("gemini", "gemini-2.0-flash-preview-image-generation", False),
        ("gemini", "text-embedding-004", False),
    ],
)
def test_is_chat_model_filters_non_text(provider_id: str, model_id: str, expected: bool) -> None:
    assert _is_chat_model(PROVIDERS[provider_id], model_id) is expected


@pytest.mark.asyncio
async def test_fetch_provider_models_filters_and_normalizes() -> None:
    payload = {
        "data": [
            {"id": "gpt-4.1-mini"},
            {"id": "models/gemini-2.0-flash"},
            {"id": "text-embedding-3-small"},
            {"id": "tts-1"},
            {"id": "dall-e-3"},
            {"id": "o3-mini"},
            {"id": ""},
            "skip-me",
        ]
    }
    response = httpx.Response(200, json=payload, request=httpx.Request("GET", "https://example.test"))

    with patch("chatlog.providers.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        client_cls.return_value = client

        models = await fetch_provider_models(
            PROVIDERS["openai"],
            "sk-test",
            timeout_seconds=5.0,
        )

    assert models == ["gpt-4.1-mini", "o3-mini"]
    client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_provider_models_raises_on_empty() -> None:
    response = httpx.Response(
        200,
        json={"data": [{"id": "text-embedding-3-small"}]},
        request=httpx.Request("GET", "https://example.test"),
    )

    with patch("chatlog.providers.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        client_cls.return_value = client

        with pytest.raises(ProviderModelsError, match="no usable chat models"):
            await fetch_provider_models(
                PROVIDERS["openai"],
                "sk-test",
                timeout_seconds=5.0,
            )


@pytest.mark.asyncio
async def test_fetch_groq_models_drops_tts_and_whisper() -> None:
    payload = {
        "data": [
            {"id": "llama-3.3-70b-versatile"},
            {"id": "whisper-large-v3"},
            {"id": "playai-tts"},
            {"id": "llama-3.1-8b-instant"},
        ]
    }
    response = httpx.Response(200, json=payload, request=httpx.Request("GET", "https://example.test"))

    with patch("chatlog.providers.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        client_cls.return_value = client

        models = await fetch_provider_models(
            PROVIDERS["groq"],
            "gsk-test",
            timeout_seconds=5.0,
        )

    assert models == ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"]


def test_list_provider_models_endpoint_uses_header_key() -> None:
    app = FastAPI()
    app.include_router(router)
    settings = Settings(groq_api_key=None, llm_timeout_seconds=5.0)

    async def fake_fetch(spec, api_key, *, timeout_seconds):
        assert spec.id == "openai"
        assert api_key == "sk-live"
        assert timeout_seconds == 5.0
        return ["gpt-4.1-mini", "gpt-4.1"]

    with (
        patch("chatlog.api.providers.get_settings", return_value=settings),
        patch("chatlog.api.providers.fetch_provider_models", side_effect=fake_fetch),
    ):
        client = TestClient(app)
        response = client.get(
            "/providers/openai/models",
            headers={"X-Provider-Api-Key": "sk-live"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "openai",
        "default_model": "gpt-4.1-mini",
        "models": ["gpt-4.1-mini", "gpt-4.1"],
    }


def test_list_provider_models_endpoint_requires_client_key() -> None:
    app = FastAPI()
    app.include_router(router)
    settings = Settings(groq_api_key=None)

    with patch("chatlog.api.providers.get_settings", return_value=settings):
        client = TestClient(app)
        response = client.get("/providers/openai/models")

    assert response.status_code == 400
    assert "API key" in response.json()["detail"]
