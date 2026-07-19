import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, TypedDict

import httpx

from chatlog.config import Settings
from chatlog.providers import get_provider, resolve_api_key, resolve_model


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class Completion:
    content: str
    input_tokens: int
    output_tokens: int


class LLMConfigurationError(RuntimeError):
    pass


class LLMProviderError(RuntimeError):
    pass


class LLMClient:
    """OpenAI-compatible client that routes by provider registry."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _resolve_api_key(self, provider_id: str, api_key: str | None) -> str:
        try:
            return resolve_api_key(get_provider(provider_id), self._settings, api_key)
        except ValueError as exc:
            raise LLMConfigurationError(str(exc)) from exc

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        provider: str,
        model: str | None = None,
        api_key: str | None = None,
    ) -> Completion:
        try:
            spec = get_provider(provider)
        except ValueError as exc:
            raise LLMConfigurationError(str(exc)) from exc

        resolved_key = self._resolve_api_key(provider, api_key)
        resolved_model = resolve_model(spec, model)
        url = f"{spec.base_url.rstrip('/')}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=self._settings.llm_timeout_seconds) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {resolved_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": resolved_model, "messages": messages},
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                usage = payload.get("usage") or {}
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError(f"LLM provider request failed: {exc}") from exc

        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("LLM provider returned an empty assistant message")

        return Completion(
            content=content,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        *,
        provider: str,
        model: str | None = None,
        api_key: str | None = None,
    ) -> AsyncIterator[tuple[str, int, int]]:
        """Yield OpenAI-compatible response deltas and the latest token usage."""
        try:
            spec = get_provider(provider)
        except ValueError as exc:
            raise LLMConfigurationError(str(exc)) from exc

        resolved_key = self._resolve_api_key(provider, api_key)
        resolved_model = resolve_model(spec, model)
        url = f"{spec.base_url.rstrip('/')}/chat/completions"
        input_tokens = 0
        output_tokens = 0

        try:
            async with httpx.AsyncClient(timeout=self._settings.llm_timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={
                        "Authorization": f"Bearer {resolved_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": resolved_model,
                        "messages": messages,
                        "stream": True,
                        "stream_options": {"include_usage": True},
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break
                        try:
                            payload = json.loads(data)
                            usage = payload.get("usage") or {}
                            input_tokens = int(usage.get("prompt_tokens", input_tokens))
                            output_tokens = int(usage.get("completion_tokens", output_tokens))
                            choices = payload.get("choices") or []
                            delta = (
                                choices[0].get("delta", {}).get("content", "") if choices else ""
                            )
                        except (
                            AttributeError,
                            IndexError,
                            TypeError,
                            ValueError,
                            json.JSONDecodeError,
                        ) as exc:
                            raise LLMProviderError(
                                f"LLM provider returned an invalid stream: {exc}"
                            ) from exc
                        if isinstance(delta, str):
                            yield delta, input_tokens, output_tokens
        except LLMProviderError:
            raise
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"LLM provider request failed: {exc}") from exc
