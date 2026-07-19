import logging
import uuid

from chatlog.api.schemas import InferenceLogCreate
from chatlog.services.log_store import LogStore
from chatlog.services.redaction import redact

logger = logging.getLogger(__name__)


class LogIngestionService:
    def __init__(self, store: LogStore) -> None:
        self._store = store

    async def ingest(self, payload: InferenceLogCreate) -> uuid.UUID:
        input_preview = redact(payload.input_preview)
        output_preview = redact(payload.output_preview)
        # Second-pass redaction is the backstop for callers that skip the SDK.
        if input_preview != payload.input_preview or output_preview != payload.output_preview:
            logger.debug(
                "Backend PII redaction changed a preview; caller may have bypassed SDK redaction"
            )
        sanitized = payload.model_copy(
            update={
                "input_preview": input_preview,
                "output_preview": output_preview,
            }
        )
        return await self._store.write(sanitized)
