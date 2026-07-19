from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


def _read(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text_from_blocks(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence):
        return ""
    parts: list[str] = []
    for block in content:
        text = _read(block, "text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Normalized input and output token counts."""

    input_tokens: int = 0
    output_tokens: int = 0


class ProviderAdapter(Protocol):
    """Provider-specific response extraction contract."""

    name: str

    def matches(self, response: object) -> bool:
        """Return whether this adapter recognizes a response object."""
        ...

    def extract_tokens(self, response: object) -> TokenUsage:
        """Extract normalized token usage from a response."""
        ...

    def extract_text(self, response: object) -> str:
        """Extract generated text from a response."""
        ...

    def extract_model(self, response: object) -> str | None:
        """Extract the provider model identifier when available."""
        ...


class OpenAIAdapter:
    """Extract metadata from OpenAI chat-completion and Responses API objects."""

    name = "openai"

    def matches(self, response: object) -> bool:
        module = type(response).__module__.lower()
        usage = _read(response, "usage")
        return module.startswith("openai") or (
            usage is not None
            and (
                _read(response, "choices") is not None
                or _read(response, "output_text") is not None
                or _read(usage, "prompt_tokens") is not None
            )
        )

    def extract_tokens(self, response: object) -> TokenUsage:
        usage = _read(response, "usage")
        return TokenUsage(
            input_tokens=int(_read(usage, "prompt_tokens", _read(usage, "input_tokens", 0)) or 0),
            output_tokens=int(
                _read(usage, "completion_tokens", _read(usage, "output_tokens", 0)) or 0
            ),
        )

    def extract_text(self, response: object) -> str:
        output_text = _read(response, "output_text")
        if isinstance(output_text, str):
            return output_text
        choices = _read(response, "choices", [])
        if isinstance(choices, Sequence) and choices:
            message = _read(choices[0], "message")
            return _text_from_blocks(_read(message, "content", ""))
        return ""

    def extract_model(self, response: object) -> str | None:
        model = _read(response, "model")
        return str(model) if model else None


class AnthropicAdapter:
    """Extract metadata from Anthropic Messages API response objects."""

    name = "anthropic"

    def matches(self, response: object) -> bool:
        module = type(response).__module__.lower()
        usage = _read(response, "usage")
        return module.startswith("anthropic") or (
            _read(response, "content") is not None
            and usage is not None
            and _read(usage, "input_tokens") is not None
        )

    def extract_tokens(self, response: object) -> TokenUsage:
        usage = _read(response, "usage")
        return TokenUsage(
            input_tokens=int(_read(usage, "input_tokens", 0) or 0),
            output_tokens=int(_read(usage, "output_tokens", 0) or 0),
        )

    def extract_text(self, response: object) -> str:
        return _text_from_blocks(_read(response, "content", []))

    def extract_model(self, response: object) -> str | None:
        model = _read(response, "model")
        return str(model) if model else None


class _FallbackAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    def matches(self, response: object) -> bool:
        return False

    def extract_tokens(self, response: object) -> TokenUsage:
        return TokenUsage()

    def extract_text(self, response: object) -> str:
        return str(response)

    def extract_model(self, response: object) -> str | None:
        model = _read(response, "model")
        return str(model) if model else None


class AdapterRegistry:
    """Resolve explicit or inferred providers without coupling core instrumentation."""

    def __init__(self, adapters: Sequence[ProviderAdapter] = ()) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ProviderAdapter) -> None:
        """Register or replace an adapter by its lowercase provider name."""
        self._adapters[adapter.name.lower()] = adapter

    def resolve(
        self,
        provider: str | None,
        response: object | None,
    ) -> ProviderAdapter:
        """Resolve an explicit provider first, otherwise inspect the response."""
        if provider is not None:
            normalized = provider.lower()
            return self._adapters.get(normalized, _FallbackAdapter(normalized))
        if response is not None:
            for adapter in self._adapters.values():
                if adapter.matches(response):
                    return adapter
        return _FallbackAdapter("unknown")
