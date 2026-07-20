import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
NonBlankString = Annotated[str, Field(min_length=1, max_length=128)]
ChatMessage = Annotated[str, Field(min_length=1, max_length=32000)]
OptionalApiKey = Annotated[str | None, Field(default=None, min_length=1, max_length=512)]


class InferenceLogCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    model: NonBlankString
    provider: NonBlankString
    conversation_id: uuid.UUID
    latency_ms: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    status: Literal["success", "error"]
    error_message: Annotated[str | None, Field(max_length=4000)] = None
    input_preview: Annotated[str, Field(max_length=8000)]
    output_preview: Annotated[str, Field(max_length=8000)]
    timestamp: datetime

    @model_validator(mode="after")
    def validate_status_and_timestamp(self) -> "InferenceLogCreate":
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        if self.status == "error" and not self.error_message:
            raise ValueError("error_message is required when status is 'error'")
        if self.status == "success" and self.error_message is not None:
            raise ValueError("error_message must be null when status is 'success'")
        return self


class InferenceLogAccepted(BaseModel):
    """Accepted for async processing via Redis Streams.

    ``stream_id`` is the Redis entry ID when XADD succeeds. ``warning`` is set
    when Redis is unreachable — the request still returns 202 so a logging
    outage cannot take down the chat path.
    """

    accepted: Literal[True] = True
    stream_id: str | None = None
    warning: str | None = None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    sequence_number: int


class ConversationCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: ChatMessage
    title: Annotated[str | None, Field(max_length=255)] = None
    provider: Annotated[str | None, Field(default=None, min_length=1, max_length=64)] = None
    model: Annotated[str | None, Field(default=None, min_length=1, max_length=128)] = None
    api_key: OptionalApiKey = None


class MessageCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: ChatMessage
    provider: Annotated[str | None, Field(default=None, min_length=1, max_length=64)] = None
    model: Annotated[str | None, Field(default=None, min_length=1, max_length=128)] = None
    api_key: OptionalApiKey = None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    status: Literal["active", "cancelled"]
    title: str | None
    provider: str | None
    model: str | None


class ConversationDetail(ConversationResponse):
    messages: list[MessageResponse]


class StatsGroup(BaseModel):
    model: str
    provider: str
    request_count: int
    avg_latency_ms: float
    error_rate: float
    input_tokens: int
    output_tokens: int
    total_tokens: int


class StatsResponse(BaseModel):
    window_start: datetime
    window_end: datetime
    groups: list[StatsGroup]


class DashboardSummary(BaseModel):
    request_count: int
    avg_latency_ms: float
    error_rate: float
    input_tokens: int
    output_tokens: int
    total_tokens: int


class LatencyPoint(BaseModel):
    timestamp: datetime
    model: str
    provider: str
    avg_latency_ms: float


class InferenceLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    model: str
    provider: str
    conversation_id: uuid.UUID
    latency_ms: int
    input_tokens: int
    output_tokens: int
    status: Literal["success", "error"]
    error_message: str | None
    created_at: datetime


class DashboardResponse(BaseModel):
    window_start: datetime
    window_end: datetime
    summary: DashboardSummary
    groups: list[StatsGroup]
    latency_points: list[LatencyPoint]
    logs: list[InferenceLogResponse]


class ProviderInfo(BaseModel):
    id: str
    label: str
    default_model: str
    requires_api_key: bool
    configured: bool


class ProvidersResponse(BaseModel):
    providers: list[ProviderInfo]


class ProviderModelsResponse(BaseModel):
    provider: str
    default_model: str
    models: list[str]
