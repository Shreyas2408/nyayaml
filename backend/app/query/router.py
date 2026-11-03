"""Authenticated query execution and owner-scoped audit history."""

import asyncio
import logging
from time import perf_counter
from typing import Annotated, Any

from asyncpg.exceptions import PostgresError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.auth.service import current_user
from app.core.rate_limit import QUERY_RATE_LIMIT_PER_HOUR, check_rate_limit
from app.db.models import User
from app.persistence.repository import list_recent, record_query_job
from app.query.dependencies import (
    QueryRunner,
    get_query_runner,
    get_session_factory,
    query_lifespan,
)
from app.query.schemas import QueryRequest, QueryResponse

logger = logging.getLogger(__name__)


class QueryRoute(APIRoute):
    """Never echo validation input or database parameters containing legal text."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def protected(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse(status_code=422, content={"detail": "Invalid query request"})
            except (SQLAlchemyError, PostgresError, OSError):
                response = JSONResponse(
                    status_code=503, content={"detail": "Query storage unavailable"}
                )
            except HTTPException as exc:
                response = JSONResponse(
                    status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        return protected


router = APIRouter(route_class=QueryRoute, lifespan=query_lifespan)
CurrentUser = Annotated[User, Depends(current_user)]
Runner = Annotated[QueryRunner, Depends(get_query_runner)]
SessionFactory = Annotated[Any, Depends(get_session_factory)]


@router.post("", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    response: Response,
    user: CurrentUser,
    runner: Runner,
    session_factory: SessionFactory,
) -> QueryResponse:
    """Execute an authorized query and commit its audit record before responding."""
    await check_rate_limit(str(user.id), "query", limit=QUERY_RATE_LIMIT_PER_HOUR)
    started = perf_counter()
    result = None
    error = None
    cancelled = False
    try:
        result = QueryResponse.model_validate(await run_in_threadpool(runner, request))
        if result.error:
            error = result.error if result.error in {"llm_unavailable", "parse_error"} else "query_processing_failed"
    except asyncio.CancelledError:
        cancelled = True
        error = "query_cancelled"
    except Exception:  # noqa: BLE001 - audit failures without exposing legal text
        # Exception strings can contain full queries, prompts, or model answers.
        error = "query_processing_failed"

    try:
        job_id = await record_query_job(
            session_factory,
            request=request,
            response=result,
            mode="api",
            latency_ms=(perf_counter() - started) * 1000,
            error=error,
            user_id=user.id,
        )
    except Exception:  # noqa: BLE001 - fail closed and redact database parameters
        # Never return a successful answer when its audit commit failed.
        raise HTTPException(503, "Query audit storage unavailable") from None

    logger.info("query_id=%s user_id=%s", job_id, user.id)
    if cancelled:
        raise asyncio.CancelledError
    if error is not None:
        raise HTTPException(
            500, "Query processing failed", headers={"X-Query-ID": str(job_id)}
        )
    response.headers["X-Query-ID"] = str(job_id)
    return result


@router.get("/history", response_model=list[dict[str, Any]])
async def get_query_history(
    user: CurrentUser,
    session_factory: SessionFactory,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[dict[str, Any]]:
    """Read only the authenticated owner's records; no public unfiltered mode."""
    jobs = await list_recent(limit=limit, user_id=user.id, session_factory=session_factory)
    return [
        {
            "id": str(job.id),
            "query_text": job.query_text,
            "act": job.act,
            "status": job.status,
            "created_at": job.created_at.isoformat(),
            "answer": job.answer,
            "citations": job.citations,
            "error": job.error,
        }
        for job in jobs
    ]
