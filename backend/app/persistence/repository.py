"""Async append-only query audit API.

The only deletion is --smoke removing its own synthetic row in a finally block.
This module records supplied responses, disclaimers and corpus identifiers; it
does not validate legal correctness. API wiring is deferred to P34-06.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

# Support the requested root-level `python -m backend.app.persistence.repository`
# while using the same canonical app modules as the committed database stack.
if __package__ == "backend.app.persistence":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.persistence.models import QueryJob
from app.query.schemas import QueryRequest, QueryResponse

_MODES = frozenset({"retrieval-only", "with-llm", "api"})
_SMOKE_QUERY = "P34-04 smoke — not a legal query"


async def record_query_job(
    session_factory=None,
    *,
    request: QueryRequest,
    response: QueryResponse | None,
    mode: str,
    latency_ms: float | None = None,
    error: str | None = None,
    corpus_sha256: str | None = None,
) -> UUID:
    """Commit one attempt in a fresh session; failed attempts need an error.

    The caller may inject an async session factory. The default is the committed
    core.database.async_session_maker, imported only after input validation.
    """
    if mode not in _MODES:
        raise ValueError("Unknown query job mode")
    status = (
        "completed"
        if response is not None and error is None and not response.error
        else "failed"
    )
    if error is None and response is not None:
        error = response.error
    if status == "failed" and (not isinstance(error, str) or not error.strip()):
        raise ValueError("A failed query job requires a non-empty error")

    fields = {}
    if response is not None:
        fields = {
            "answer": response.answer,
            "query_type": response.query_type,
            "fallback_used": response.fallback_used,
            "citations": [c.model_dump(mode="json") for c in response.citations],
            "retrieved_citation_ids": list(response.retrieved_citation_ids),
            "citations_valid": response.citations_valid,
            "hallucination_flags": list(response.hallucination_flags),
            "disclaimers": list(response.disclaimers),
        }
    job = QueryJob(
        query_text=request.query,
        act=request.act,
        top_k=request.top_k,
        mode=mode,
        status=status,
        latency_ms=latency_ms,
        error=error,
        corpus_sha256=corpus_sha256,
        **fields,
    )
    if session_factory is None:
        from app.core.database import async_session_maker

        session_factory = async_session_maker
    async with session_factory() as session:
        try:
            session.add(job)
            await session.flush()
            job_id = job.id
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
    return job_id


async def get_query_job(job_id: UUID) -> QueryJob | None:
    """Read one audit record in a fresh session."""
    from app.core.database import async_session_maker

    async with async_session_maker() as session:
        return await session.get(QueryJob, job_id)


async def list_recent(limit: int = 50) -> list[QueryJob]:
    """Read newest records first, breaking timestamp ties by UUID."""
    if limit < 1:
        raise ValueError("limit must be positive")
    from app.core.database import async_session_maker

    async with async_session_maker() as session:
        result = await session.scalars(
            select(QueryJob)
            .order_by(QueryJob.created_at.desc(), QueryJob.id.desc())
            .limit(limit)
        )
        return list(result.all())


async def _smoke() -> dict:
    from app.core.database import async_session_maker, engine
    from app.evaluation.scorer import DISCLAIMERS

    job_id = None
    try:
        manifest = Path(__file__).resolve().parents[3] / "data/benchmarks/MANIFEST.json"
        try:
            corpus_sha256 = json.loads(manifest.read_text(encoding="utf-8"))[
                "corpus_sha256"
            ]
        except (OSError, ValueError, KeyError):
            corpus_sha256 = None
        request = QueryRequest(query=_SMOKE_QUERY, act=None, top_k=1)
        response = QueryResponse(
            answer="synthetic smoke answer", disclaimers=DISCLAIMERS
        )
        try:
            job_id = await record_query_job(
                request=request,
                response=response,
                mode="api",
                corpus_sha256=corpus_sha256,
            )
            job = await get_query_job(job_id)
            expected = {
                "id": job_id,
                "query_text": request.query,
                "act": request.act,
                "top_k": request.top_k,
                "mode": "api",
                "status": "completed",
                "answer": response.answer,
                "query_type": response.query_type,
                "fallback_used": response.fallback_used,
                "citations": [c.model_dump(mode="json") for c in response.citations],
                "retrieved_citation_ids": response.retrieved_citation_ids,
                "citations_valid": response.citations_valid,
                "hallucination_flags": response.hallucination_flags,
                "disclaimers": response.disclaimers,
                "corpus_sha256": corpus_sha256,
                "latency_ms": None,
                "error": None,
                "user_id": None,
            }
            if job is None or any(
                getattr(job, key) != value for key, value in expected.items()
            ):
                raise ValueError("Smoke round-trip mismatch")
            if job.created_at is None or job.created_at.utcoffset() is None:
                raise ValueError("Smoke timestamp missing or without timezone")
            output = {
                "id": str(job.id),
                "query_text": job.query_text,
                "status": job.status,
                "mode": job.mode,
                "created_at": job.created_at.isoformat(),
                "disclaimers": job.disclaimers,
                "corpus_sha256": job.corpus_sha256,
            }
        finally:
            if job_id is not None:
                # Sole append-only exception: delete exactly this smoke run's row.
                async with async_session_maker() as session:
                    try:
                        await session.execute(
                            QueryJob.__table__.delete().where(QueryJob.id == job_id)
                        )
                        await session.commit()
                    except BaseException:
                        await session.rollback()
                        raise
                if await get_query_job(job_id) is not None:
                    raise ValueError("Smoke cleanup failed")
        return {**output, "cleanup": "ok"}
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify query audit persistence and clean up its row"
    )
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.parse_args(argv)
    try:
        result = asyncio.run(_smoke())
    except Exception as exc:  # noqa: BLE001 - CLI must exit 2 without exposing DB details
        # DB exception strings can contain connection details or bound parameters.
        print(
            f"P34-04 smoke failed ({type(exc).__name__}); check database connectivity, "
            "migration 0002 and smoke round-trip/cleanup.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
