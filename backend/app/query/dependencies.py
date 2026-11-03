"""Query-specific wiring for the existing synchronous pipeline and async store."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from threading import Lock

from starlette.concurrency import run_in_threadpool

from app.core.database import async_session_maker
from app.query.schemas import QueryRequest, QueryResponse

QueryRunner = Callable[[QueryRequest], QueryResponse]
_pipeline_lock = Lock()


@lru_cache(maxsize=1)
def _orchestrator():
    # Import and construct in the worker, so setup failures are also audited.
    from app.query.orchestrator import QueryOrchestrator

    return QueryOrchestrator(allow_download=False)


def _run(request: QueryRequest) -> QueryResponse:
    # Initialization and the existing mutable model/LLM caches are serialized.
    # The router offloads this entire synchronous call from the event loop.
    with _pipeline_lock:
        return _orchestrator().run(request)


def get_query_runner() -> QueryRunner:
    return _run


def get_session_factory():
    """Independent audit/history sessions, not the auth dependency's session."""
    return async_session_maker


def _close() -> None:
    with _pipeline_lock:
        if _orchestrator.cache_info().currsize:
            _orchestrator().close()
            _orchestrator.cache_clear()


@asynccontextmanager
async def query_lifespan(_app):
    try:
        yield
    finally:
        await run_in_threadpool(_close)
