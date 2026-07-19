from fastapi import APIRouter, HTTPException, status

from chatlog.api.dependencies import LogStoreDependency
from chatlog.api.schemas import InferenceLogAccepted, InferenceLogCreate
from chatlog.services.ingestion import LogIngestionService
from chatlog.services.log_store import ConversationNotFoundError

router = APIRouter(prefix="/logs", tags=["logs"])


@router.post(
    "/ingest",
    response_model=InferenceLogAccepted,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_log(
    payload: InferenceLogCreate,
    store: LogStoreDependency,
) -> InferenceLogAccepted:
    # A direct async commit keeps the take-home deployment simple while avoiding
    # thread blocking. At higher volume, this boundary can enqueue to Kafka and
    # return 202, trading immediate durability feedback for lower tail latency.
    try:
        log_id = await LogIngestionService(store).ingest(payload)
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="conversation_id does not reference an existing conversation",
        ) from exc
    return InferenceLogAccepted(id=log_id)
