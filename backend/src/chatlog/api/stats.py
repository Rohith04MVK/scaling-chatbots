from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from chatlog.api.dependencies import SessionDependency
from chatlog.api.schemas import (
    DashboardResponse,
    DashboardSummary,
    InferenceLogResponse,
    LatencyPoint,
    StatsGroup,
    StatsResponse,
)
from chatlog.models import InferenceLog

router = APIRouter(tags=["stats"])


def _window(
    *,
    window_hours: int | None = None,
    window_minutes: int | None = None,
) -> tuple[datetime, datetime]:
    window_end = datetime.now(UTC)
    duration = (
        timedelta(minutes=window_minutes)
        if window_minutes is not None
        else timedelta(hours=window_hours or 24)
    )
    return window_end - duration, window_end


async def _stats_groups(
    session: SessionDependency,
    window_start: datetime,
    window_end: datetime,
) -> list[StatsGroup]:
    statement = (
        select(
            InferenceLog.model,
            InferenceLog.provider,
            func.count().label("request_count"),
            func.avg(InferenceLog.latency_ms).label("avg_latency_ms"),
            func.avg(case((InferenceLog.status == "error", 1.0), else_=0.0)).label("error_rate"),
            func.sum(InferenceLog.input_tokens).label("input_tokens"),
            func.sum(InferenceLog.output_tokens).label("output_tokens"),
        )
        .where(
            InferenceLog.created_at >= window_start,
            InferenceLog.created_at <= window_end,
        )
        .group_by(InferenceLog.model, InferenceLog.provider)
        .order_by(InferenceLog.model, InferenceLog.provider)
    )
    rows = (await session.execute(statement)).all()
    return [
        StatsGroup(
            model=row.model,
            provider=row.provider,
            request_count=row.request_count,
            avg_latency_ms=float(row.avg_latency_ms),
            error_rate=float(row.error_rate),
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            total_tokens=row.input_tokens + row.output_tokens,
        )
        for row in rows
    ]


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    session: SessionDependency,
    window_hours: int = Query(default=24, ge=1, le=24 * 365),
) -> StatsResponse:
    window_start, window_end = _window(window_hours=window_hours)
    groups = await _stats_groups(session, window_start, window_end)
    return StatsResponse(window_start=window_start, window_end=window_end, groups=groups)


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    session: SessionDependency,
    window_minutes: int = Query(default=60, ge=1, le=24 * 365),
    limit: int = Query(default=50, ge=1, le=200),
) -> DashboardResponse:
    window_start, window_end = _window(window_minutes=window_minutes)
    groups = await _stats_groups(session, window_start, window_end)
    summary_statement = (
        select(
            func.count().label("request_count"),
            func.avg(InferenceLog.latency_ms).label("avg_latency_ms"),
            func.avg(case((InferenceLog.status == "error", 1.0), else_=0.0)).label("error_rate"),
            func.sum(InferenceLog.input_tokens).label("input_tokens"),
            func.sum(InferenceLog.output_tokens).label("output_tokens"),
        )
        .where(
            InferenceLog.created_at >= window_start,
            InferenceLog.created_at <= window_end,
        )
    )
    summary_row = (await session.execute(summary_statement)).one()
    summary = DashboardSummary(
        request_count=summary_row.request_count or 0,
        avg_latency_ms=float(summary_row.avg_latency_ms or 0),
        error_rate=float(summary_row.error_rate or 0),
        input_tokens=summary_row.input_tokens or 0,
        output_tokens=summary_row.output_tokens or 0,
        total_tokens=(summary_row.input_tokens or 0) + (summary_row.output_tokens or 0),
    )
    bucket = func.date_trunc("minute", InferenceLog.created_at).label("timestamp")
    points_statement = (
        select(
            bucket,
            InferenceLog.model,
            InferenceLog.provider,
            func.avg(InferenceLog.latency_ms).label("avg_latency_ms"),
        )
        .where(
            InferenceLog.created_at >= window_start,
            InferenceLog.created_at <= window_end,
        )
        .group_by(bucket, InferenceLog.model, InferenceLog.provider)
        .order_by(bucket, InferenceLog.model, InferenceLog.provider)
    )
    point_rows = (await session.execute(points_statement)).all()
    logs_statement = (
        select(InferenceLog)
        .where(
            InferenceLog.created_at >= window_start,
            InferenceLog.created_at <= window_end,
        )
        .order_by(InferenceLog.created_at.desc())
        .limit(limit)
    )
    logs = list((await session.scalars(logs_statement)).all())
    return DashboardResponse(
        window_start=window_start,
        window_end=window_end,
        summary=summary,
        groups=groups,
        latency_points=[
            LatencyPoint(
                timestamp=row.timestamp,
                model=row.model,
                provider=row.provider,
                avg_latency_ms=float(row.avg_latency_ms),
            )
            for row in point_rows
        ],
        logs=[InferenceLogResponse.model_validate(log) for log in logs],
    )
