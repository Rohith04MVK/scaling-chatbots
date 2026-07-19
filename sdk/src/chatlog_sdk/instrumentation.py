import functools
import inspect
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, TypeVar, cast

from chatlog_sdk.adapters import (
    AdapterRegistry,
    AnthropicAdapter,
    OpenAIAdapter,
    ProviderAdapter,
    TokenUsage,
)
from chatlog_sdk.config import SDKConfig
from chatlog_sdk.context import get_conversation_id
from chatlog_sdk.delivery import BackgroundDelivery, LogSender

F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T")
InputExtractor = Callable[[tuple[Any, ...], dict[str, Any]], object]


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return repr(value)
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"


def _default_input(args: tuple[Any, ...], kwargs: dict[str, Any]) -> object:
    for name in ("prompt", "messages", "input", "content"):
        if name in kwargs:
            return kwargs[name]
    if len(args) == 1:
        return args[0]
    return {"args": args, "kwargs": kwargs}


def _normalize_conversation_id(value: object | None) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass(slots=True)
class _CallState:
    started_at: datetime
    started_counter: float
    conversation_id: str | None
    model_hint: str | None
    provider_hint: str | None
    input_value: object


class InstrumentationClient:
    """Configure and create LLM instrumentation wrappers."""

    def __init__(
        self,
        *,
        backend_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        preview_chars: int | None = None,
        adapters: Sequence[ProviderAdapter] | None = None,
        sender: LogSender | None = None,
    ) -> None:
        """Create a client using explicit settings over environment defaults."""
        self.config = SDKConfig.from_env(
            backend_url=backend_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            preview_chars=preview_chars,
        )
        configured_adapters = (
            list(adapters) if adapters is not None else [OpenAIAdapter(), AnthropicAdapter()]
        )
        self._registry = AdapterRegistry(configured_adapters)
        self._delivery = BackgroundDelivery(self.config, sender)

    def register_adapter(self, adapter: ProviderAdapter) -> None:
        """Register a provider adapter without changing instrumentation code."""
        self._registry.register(adapter)

    def instrument(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        conversation_id: str | uuid.UUID | None = None,
        input_extractor: InputExtractor | None = None,
    ) -> Callable[[F], F]:
        """Decorate a sync or async LLM callable and preserve its behavior and type."""

        def decorator(function: F) -> F:
            if inspect.iscoroutinefunction(function):

                @functools.wraps(function)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    state = self._start_call(
                        args=args,
                        kwargs=kwargs,
                        provider=provider,
                        model=model,
                        conversation_id=conversation_id,
                        input_extractor=input_extractor,
                    )
                    try:
                        response = await function(*args, **kwargs)
                    except BaseException as exc:
                        self._finish_call(state, response=None, error=exc)
                        raise
                    self._finish_call(state, response=response, error=None)
                    return response

                return cast(F, async_wrapper)

            @functools.wraps(function)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                state = self._start_call(
                    args=args,
                    kwargs=kwargs,
                    provider=provider,
                    model=model,
                    conversation_id=conversation_id,
                    input_extractor=input_extractor,
                )
                try:
                    response = function(*args, **kwargs)
                except BaseException as exc:
                    self._finish_call(state, response=None, error=exc)
                    raise
                self._finish_call(state, response=response, error=None)
                return response

            return cast(F, sync_wrapper)

        return decorator

    def instrumented_call(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        conversation_id: str | uuid.UUID | None = None,
        input_data: object = "",
    ) -> "InstrumentedCall":
        """Create a sync/async context manager for an inline LLM request."""
        return InstrumentedCall(
            client=self,
            provider=provider,
            model=model,
            conversation_id=conversation_id,
            input_data=input_data,
        )

    def flush(self, timeout: float = 1.0) -> bool:
        """Wait briefly for queued logs, primarily for tests and short-lived scripts."""
        return self._delivery.flush(timeout)

    def close(self, timeout: float = 1.0) -> None:
        """Best-effort flush and release the background transport."""
        self._delivery.close(timeout)

    def _start_call(
        self,
        *,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        provider: str | None,
        model: str | None,
        conversation_id: str | uuid.UUID | None,
        input_extractor: InputExtractor | None,
    ) -> _CallState:
        resolved_conversation_id = _normalize_conversation_id(
            conversation_id
            or kwargs.get("conversation_id")
            or kwargs.get("session_id")
            or get_conversation_id()
        )
        model_hint = model or _optional_string(kwargs.get("model"))
        try:
            input_value = (
                input_extractor(args, kwargs)
                if input_extractor is not None
                else _default_input(args, kwargs)
            )
        except Exception:
            input_value = "<input extraction failed>"
        return _CallState(
            started_at=datetime.now(UTC),
            started_counter=time.perf_counter(),
            conversation_id=resolved_conversation_id,
            model_hint=model_hint,
            provider_hint=provider,
            input_value=input_value,
        )

    def _finish_call(
        self,
        state: _CallState,
        *,
        response: object | None,
        error: BaseException | None,
    ) -> None:
        try:
            ended_at = datetime.now(UTC)
            latency_ms = max(0, round((time.perf_counter() - state.started_counter) * 1000))
            adapter = self._registry.resolve(state.provider_hint, response)
            if error is None and response is not None:
                usage = adapter.extract_tokens(response)
                output_value = adapter.extract_text(response)
                status = "success"
                error_message = None
            else:
                usage = TokenUsage()
                output_value = ""
                status = "error"
                error_message = (
                    f"{type(error).__name__}: {error}" if error is not None else "Unknown error"
                )[:4000]

            model = state.model_hint or (
                adapter.extract_model(response) if response is not None else None
            )
            payload: Mapping[str, Any] = {
                "model": model or "unknown",
                "provider": adapter.name,
                "conversation_id": state.conversation_id,
                "latency_ms": latency_ms,
                "input_tokens": max(0, usage.input_tokens),
                "output_tokens": max(0, usage.output_tokens),
                "status": status,
                "error_message": error_message,
                "input_preview": self._preview(state.input_value),
                "output_preview": self._preview(output_value),
                "timestamp": ended_at.isoformat(),
            }
            # The backend requires a real conversation FK. Missing context drops
            # only telemetry instead of generating a guaranteed-invalid request.
            if state.conversation_id is not None:
                self._delivery.submit(payload)
        except Exception:
            # Instrumentation must never alter the wrapped call's result.
            return

    def _preview(self, value: object) -> str:
        # Soft-cap only on the request path. Presidio redaction and the final
        # preview_chars truncate run in the background delivery worker.
        return _stringify(value)[:8000]


class InstrumentedCall(
    AbstractContextManager["InstrumentedCall"],
    AbstractAsyncContextManager["InstrumentedCall"],
):
    """Context manager that records a response assigned with ``set_response``."""

    def __init__(
        self,
        *,
        client: InstrumentationClient,
        provider: str | None,
        model: str | None,
        conversation_id: str | uuid.UUID | None,
        input_data: object,
    ) -> None:
        self._client = client
        self._provider = provider
        self._model = model
        self._conversation_id = conversation_id
        self._input_data = input_data
        self._state: _CallState | None = None
        self._response: object | None = None

    def set_response(self, response: T) -> T:
        """Attach the provider response for text and token extraction."""
        self._response = response
        return response

    def __enter__(self) -> "InstrumentedCall":
        self._state = self._client._start_call(
            args=(),
            kwargs={},
            provider=self._provider,
            model=self._model,
            conversation_id=self._conversation_id,
            input_extractor=lambda _args, _kwargs: self._input_data,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if self._state is not None:
            self._client._finish_call(
                self._state,
                response=self._response,
                error=exc_value,
            )
        return False

    async def __aenter__(self) -> "InstrumentedCall":
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return self.__exit__(exc_type, exc_value, traceback)


def _optional_string(value: object | None) -> str | None:
    return str(value) if value is not None else None


_default_client: InstrumentationClient | None = None
_default_client_lock = threading.Lock()


def _get_default_client() -> InstrumentationClient:
    global _default_client
    if _default_client is None:
        with _default_client_lock:
            if _default_client is None:
                _default_client = InstrumentationClient()
    return _default_client


def instrument(
    *,
    provider: str | None = None,
    model: str | None = None,
    conversation_id: str | uuid.UUID | None = None,
    input_extractor: InputExtractor | None = None,
    client: InstrumentationClient | None = None,
) -> Callable[[F], F]:
    """Decorate a sync or async LLM callable using the default or supplied client."""
    return (client or _get_default_client()).instrument(
        provider=provider,
        model=model,
        conversation_id=conversation_id,
        input_extractor=input_extractor,
    )


def instrumented_call(
    *,
    provider: str | None = None,
    model: str | None = None,
    conversation_id: str | uuid.UUID | None = None,
    input_data: object = "",
    client: InstrumentationClient | None = None,
) -> InstrumentedCall:
    """Create an instrumentation context manager around an inline LLM call."""
    return (client or _get_default_client()).instrumented_call(
        provider=provider,
        model=model,
        conversation_id=conversation_id,
        input_data=input_data,
    )
