"""Redis Streams consumer: inference_logs → redact → Postgres.

Uses a consumer group so acknowledged vs pending messages are tracked; after N
failed delivery attempts a message is moved to the DLQ stream instead of
retrying forever.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from chatlog.config import Settings
from chatlog.db.session import async_session_factory
from chatlog.services.ingestion import LogIngestionService
from chatlog.services.log_store import PostgresLogStore
from chatlog.services.stream import STREAM_PAYLOAD_FIELD, parse_stream_payload

logger = logging.getLogger(__name__)

# How long a pending message must sit idle before we reclaim it for retry / DLQ.
CLAIM_MIN_IDLE_MS = 5_000
READ_BLOCK_MS = 2_000
READ_COUNT = 16


class InferenceLogConsumer:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                name=self._settings.redis_stream,
                groupname=self._settings.redis_consumer_group,
                id="0",
                mkstream=True,
            )
            logger.info(
                "Created consumer group %s on stream %s",
                self._settings.redis_consumer_group,
                self._settings.redis_stream,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            logger.debug("Consumer group already exists")

    async def run(self) -> None:
        await self.ensure_group()
        logger.info(
            "Consumer %s listening on stream=%s group=%s",
            self._settings.redis_consumer_name,
            self._settings.redis_stream,
            self._settings.redis_consumer_group,
        )
        while not self._stop.is_set():
            try:
                await self._claim_stale()
                await self._read_new()
            except Exception:
                logger.exception("Consumer loop error; backing off briefly")
                await asyncio.sleep(1.0)

    async def _read_new(self) -> None:
        results = await self._redis.xreadgroup(
            groupname=self._settings.redis_consumer_group,
            consumername=self._settings.redis_consumer_name,
            streams={self._settings.redis_stream: ">"},
            count=READ_COUNT,
            block=READ_BLOCK_MS,
        )
        if not results:
            return
        for _stream_name, entries in results:
            for entry_id, fields in entries:
                # Fresh deliveries start at count 1 for DLQ accounting.
                await self._handle_entry(entry_id, fields, delivery_count=1)

    async def _claim_stale(self) -> None:
        """Reclaim idle pending messages for retry or DLQ."""
        try:
            claimed = await self._redis.xautoclaim(
                name=self._settings.redis_stream,
                groupname=self._settings.redis_consumer_group,
                consumername=self._settings.redis_consumer_name,
                min_idle_time=CLAIM_MIN_IDLE_MS,
                start_id="0-0",
                count=READ_COUNT,
            )
        except ResponseError:
            # Older Redis or empty stream edge cases — skip this tick.
            return

        # redis-py returns (next_id, [(id, fields), ...], deleted_ids) on recent versions.
        entries: list[tuple[str, dict[str, Any]]]
        if isinstance(claimed, (list, tuple)) and len(claimed) >= 2:
            entries = claimed[1] or []
        else:
            entries = []

        for entry_id, fields in entries:
            delivery_count = await self._delivery_count(entry_id)
            await self._handle_entry(entry_id, fields, delivery_count=delivery_count)

    async def _delivery_count(self, entry_id: str) -> int:
        pending = await self._redis.xpending_range(
            name=self._settings.redis_stream,
            groupname=self._settings.redis_consumer_group,
            min=entry_id,
            max=entry_id,
            count=1,
        )
        if not pending:
            return 1
        item = pending[0]
        if isinstance(item, dict):
            return int(item.get("times_delivered") or item.get("delivery_count") or 1)
        # Tuple form: (message_id, consumer, time_since_delivered, times_delivered)
        if isinstance(item, (list, tuple)) and len(item) >= 4:
            return int(item[3])
        return 1

    async def _handle_entry(
        self,
        entry_id: str,
        fields: dict[str, Any],
        *,
        delivery_count: int,
    ) -> None:
        try:
            payload = parse_stream_payload(fields)
            async with async_session_factory() as session:
                await LogIngestionService(PostgresLogStore(session)).ingest(payload)
            await self._redis.xack(
                self._settings.redis_stream,
                self._settings.redis_consumer_group,
                entry_id,
            )
            logger.info("Persisted inference log from stream entry %s", entry_id)
        except Exception:
            logger.exception(
                "Failed processing stream entry %s (delivery_count=%s)",
                entry_id,
                delivery_count,
            )
            if delivery_count >= self._settings.redis_max_delivery_attempts:
                await self._move_to_dlq(entry_id, fields, delivery_count=delivery_count)
            # Otherwise leave unacked so XAUTOCLAIM can retry.

    async def _move_to_dlq(
        self,
        entry_id: str,
        fields: dict[str, Any],
        *,
        delivery_count: int,
    ) -> None:
        raw = fields.get(STREAM_PAYLOAD_FIELD, "")
        if isinstance(raw, bytes):
            raw = raw.decode()
        await self._redis.xadd(
            self._settings.redis_dlq_stream,
            {
                STREAM_PAYLOAD_FIELD: raw,
                "source_id": entry_id,
                "delivery_count": str(delivery_count),
            },
        )
        await self._redis.xack(
            self._settings.redis_stream,
            self._settings.redis_consumer_group,
            entry_id,
        )
        logger.error(
            "Moved stream entry %s to DLQ %s after %s failed attempts "
            "(hook alerting here in production)",
            entry_id,
            self._settings.redis_dlq_stream,
            delivery_count,
        )
