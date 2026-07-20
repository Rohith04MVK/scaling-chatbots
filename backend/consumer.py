"""Entrypoint for the independent Redis Streams → Postgres consumer process."""

from __future__ import annotations

import asyncio
import logging
import signal

from chatlog.config import get_settings
from chatlog.db.session import engine
from chatlog.services.stream_consumer import InferenceLogConsumer
from redis.asyncio import Redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("chatlog.consumer")


async def main() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    consumer = InferenceLogConsumer(redis, settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, consumer.request_stop)
        except NotImplementedError:
            # Windows / restricted environments: rely on KeyboardInterrupt.
            pass
    try:
        await consumer.run()
    finally:
        await redis.aclose()
        await engine.dispose()
        logger.info("Consumer shut down")


if __name__ == "__main__":
    asyncio.run(main())
