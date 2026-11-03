"""Authenticated access to the verbatim 2023 statutory snapshot."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from app.auth.service import current_user
from app.core.rate_limit import SECTIONS_RATE_LIMIT_PER_HOUR, check_rate_limit
from app.db.models import User
from app.evaluation.scorer import DISCLAIMERS
from app.sections import service

router = APIRouter(prefix="/api/v1/sections", tags=["sections"])
CurrentUser = Annotated[User, Depends(current_user)]
_SNAPSHOT = {"source_snapshot": "2023", "disclaimer": DISCLAIMERS[1]}


@router.get("/acts")
async def acts(user: CurrentUser, response: Response) -> dict:
    await check_rate_limit(str(user.id), "sections", SECTIONS_RATE_LIMIT_PER_HOUR)
    try:
        records = await run_in_threadpool(service.list_acts)
    except service.CorpusUnavailable:
        raise HTTPException(503, "Statutory snapshot unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return {"acts": records, **_SNAPSHOT}


@router.get("/{act}/{section}")
async def section(act: str, section: str, user: CurrentUser, response: Response) -> dict:
    await check_rate_limit(str(user.id), "sections", SECTIONS_RATE_LIMIT_PER_HOUR)
    try:
        record = await run_in_threadpool(service.get_section, act, section)
    except service.CorpusUnavailable:
        raise HTTPException(503, "Statutory snapshot unavailable") from None
    if record is None:
        raise HTTPException(404, "Section not found")
    response.headers["Cache-Control"] = "no-store"
    return {**record, **_SNAPSHOT}
