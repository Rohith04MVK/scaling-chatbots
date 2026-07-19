import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class SDKConfig:
    """Runtime settings for inference instrumentation and log delivery."""

    backend_url: str
    api_key: str | None
    timeout_seconds: float
    retry_backoff_seconds: float
    preview_chars: int
    queue_capacity: int

    @classmethod
    def from_env(
        cls,
        *,
        backend_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        preview_chars: int | None = None,
    ) -> "SDKConfig":
        """Build settings from explicit overrides and environment defaults."""
        configured_url = backend_url or os.getenv("LLM_LOG_BACKEND_URL", "http://localhost:8000")
        return cls(
            backend_url=configured_url.rstrip("/"),
            api_key=api_key if api_key is not None else os.getenv("LLM_LOG_API_KEY"),
            timeout_seconds=max(
                0.05,
                timeout_seconds
                if timeout_seconds is not None
                else _env_float("LLM_LOG_TIMEOUT_SECONDS", 1.0),
            ),
            retry_backoff_seconds=max(0.0, _env_float("LLM_LOG_RETRY_BACKOFF_SECONDS", 0.1)),
            preview_chars=max(
                0,
                preview_chars
                if preview_chars is not None
                else _env_int("LLM_LOG_PREVIEW_CHARS", 1000),
            ),
            queue_capacity=max(1, _env_int("LLM_LOG_QUEUE_CAPACITY", 1000)),
        )

    @property
    def ingest_url(self) -> str:
        """Return the fully qualified inference-ingestion endpoint."""
        return f"{self.backend_url}/logs/ingest"
