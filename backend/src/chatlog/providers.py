from dataclasses import dataclass
from typing import Literal

import httpx

from chatlog.config import Settings

AuthMode = Literal["server", "client"]


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    base_url: str
    default_model: str
    auth: AuthMode
    # When set, only model ids matching one of these prefixes are returned.
    model_prefixes: tuple[str, ...] = ()


PROVIDERS: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        id="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        auth="server",
    ),
    "openai": ProviderSpec(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4.1-mini",
        auth="client",
        model_prefixes=("gpt-", "o1", "o3", "o4", "chatgpt-"),
    ),
    "gemini": ProviderSpec(
        id="gemini",
        label="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.0-flash",
        auth="client",
        model_prefixes=("gemini-",),
    ),
}


class ProviderModelsError(RuntimeError):
    pass


def get_provider(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"Unknown provider '{provider_id}'") from exc


def is_provider_configured(spec: ProviderSpec, settings: Settings) -> bool:
    if spec.auth == "server":
        return bool(settings.groq_api_key)
    return True


def default_provider_id(settings: Settings) -> str:
    groq = PROVIDERS["groq"]
    if is_provider_configured(groq, settings):
        return groq.id
    return "openai"


def resolve_model(spec: ProviderSpec, model: str | None) -> str:
    if model is None or not model.strip():
        return spec.default_model
    return model.strip()


def resolve_api_key(spec: ProviderSpec, settings: Settings, api_key: str | None) -> str:
    if spec.auth == "server":
        if not settings.groq_api_key:
            raise ValueError(
                "Groq is not configured. Set CHATLOG_GROQ_API_KEY on the server."
            )
        return settings.groq_api_key

    if not api_key or not api_key.strip():
        raise ValueError(f"{spec.label} requires an API key. Paste your key in the chat composer.")
    return api_key.strip()


def _normalize_model_id(model_id: str) -> str:
    return model_id.removeprefix("models/")


# Substrings that mark non-text / non-chat models across providers.
_NON_TEXT_MARKERS: tuple[str, ...] = (
    "whisper",
    "tts",
    "speech",
    "transcribe",
    "audio",
    "realtime",
    "embedding",
    "embed",
    "moderation",
    "dall-e",
    "dalle",
    "imagen",
    "image",
    "sora",
    "flux",
    "playai",
    "guard",
)


def _is_non_text_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(marker in lowered for marker in _NON_TEXT_MARKERS)


def _is_chat_model(spec: ProviderSpec, model_id: str) -> bool:
    if _is_non_text_model(model_id):
        return False
    if not spec.model_prefixes:
        return True
    return model_id.startswith(spec.model_prefixes)


def pick_default_model(models: list[str], preferred: str) -> str:
    if preferred in models:
        return preferred
    return models[0] if models else preferred


async def fetch_provider_models(
    spec: ProviderSpec,
    api_key: str,
    *,
    timeout_seconds: float,
) -> list[str]:
    """List models from the provider's OpenAI-compatible /models endpoint."""
    url = f"{spec.base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()
            raw_items = payload.get("data") or []
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise ProviderModelsError(f"Failed to list models from {spec.label}: {exc}") from exc

    models: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        model_id = _normalize_model_id(raw_id.strip())
        if model_id in seen or not _is_chat_model(spec, model_id):
            continue
        seen.add(model_id)
        models.append(model_id)

    models.sort()
    if not models:
        raise ProviderModelsError(f"{spec.label} returned no usable chat models")
    return models
