import logging

from fastapi import APIRouter, status

from chatlog.api.schemas import InferenceLogAccepted, InferenceLogCreate
from chatlog.services.stream import StreamPublishError, publish_inference_log

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["logs"])


@router.post(
    "/ingest",
    response_model=InferenceLogAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_log(payload: InferenceLogCreate) -> InferenceLogAccepted:
    """Validate at the edge, then XADD onto the inference Redis Stream.

    The producer never writes Postgres. Redaction and persistence happen in the
    independent consumer process after it reads from the stream.
    """
    try:
        stream_id = await publish_inference_log(payload)
    except StreamPublishError:
        logger.exception("Redis unreachable while enqueueing inference log")
        return InferenceLogAccepted(
            warning="redis_unavailable: log not enqueued; chat path unaffected",
        )
    return InferenceLogAccepted(stream_id=stream_id)
