import logging
import queue
import threading
import time
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from chatlog_sdk.config import SDKConfig
from chatlog_sdk.redaction import redact

logger = logging.getLogger(__name__)
_STOP = object()


def _redact_previews(payload: Mapping[str, Any], *, preview_chars: int) -> dict[str, Any]:
    """Redact free-text preview fields on the delivery worker, not the request path."""
    sanitized = dict(payload)
    input_preview = sanitized.get("input_preview")
    output_preview = sanitized.get("output_preview")
    if isinstance(input_preview, str):
        sanitized["input_preview"] = redact(input_preview)[:preview_chars]
    if isinstance(output_preview, str):
        sanitized["output_preview"] = redact(output_preview)[:preview_chars]
    return sanitized


class LogSender(Protocol):
    def send(self, payload: Mapping[str, Any]) -> None: ...

    def close(self) -> None: ...


class HttpLogSender:
    def __init__(self, config: SDKConfig) -> None:
        self._config = config
        self._client: httpx.Client | None = None

    def send(self, payload: Mapping[str, Any]) -> None:
        if self._client is None:
            headers = (
                {"Authorization": f"Bearer {self._config.api_key}"}
                if self._config.api_key
                else None
            )
            self._client = httpx.Client(
                timeout=self._config.timeout_seconds,
                headers=headers,
            )
        response = self._client.post(self._config.ingest_url, json=payload)
        response.raise_for_status()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class BackgroundDelivery:
    """Bounded, daemon-thread delivery that never raises into application code."""

    def __init__(self, config: SDKConfig, sender: LogSender | None = None) -> None:
        self._config = config
        self._sender = sender or HttpLogSender(config)
        self._queue: queue.Queue[Mapping[str, Any] | object] = queue.Queue(
            maxsize=config.queue_capacity
        )
        self._start_lock = threading.Lock()
        self._started = False
        self._closed = False

    def submit(self, payload: Mapping[str, Any]) -> None:
        """Enqueue without waiting; drop the event if the local queue is full."""
        if self._closed:
            return
        self._ensure_started()
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            logger.debug("Inference log queue is full; dropping event")

    def flush(self, timeout: float = 1.0) -> bool:
        """Wait briefly for queued events; intended for tests and short-lived scripts."""
        deadline = time.monotonic() + max(0.0, timeout)
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        return self._queue.unfinished_tasks == 0

    def close(self, timeout: float = 1.0) -> None:
        """Best-effort flush and stop the worker without raising delivery errors."""
        if self._closed:
            return
        self.flush(timeout)
        self._closed = True
        if self._started:
            try:
                self._queue.put_nowait(_STOP)
            except queue.Full:
                pass
        else:
            self._sender.close()

    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._start_lock:
            if self._started:
                return
            thread = threading.Thread(
                target=self._run,
                name="chatlog-sdk-delivery",
                daemon=True,
            )
            thread.start()
            self._started = True

    def _run(self) -> None:
        try:
            while True:
                item = self._queue.get()
                try:
                    if item is _STOP:
                        return
                    self._send_with_retry(item)
                finally:
                    self._queue.task_done()
        finally:
            # The worker owns the HTTP client, so it also closes it.
            self._sender.close()

    def _send_with_retry(self, payload: Mapping[str, Any]) -> None:
        sanitized = _redact_previews(payload, preview_chars=self._config.preview_chars)
        for attempt in range(2):
            try:
                self._sender.send(sanitized)
                return
            except Exception:
                if attempt == 0:
                    time.sleep(self._config.retry_backoff_seconds)
                else:
                    logger.debug("Inference log delivery failed after retry", exc_info=True)
