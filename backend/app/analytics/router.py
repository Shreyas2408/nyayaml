"""Owner-scoped usage counts and latencies, without legal text."""

from typing import Annotated

from asyncpg.exceptions import PostgresError
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError

from app.analytics.service import compute_summary
from app.auth.service import current_user
from app.core.rate_limit import ANALYTICS_RATE_LIMIT_PER_HOUR, check_rate_limit
from app.db.models import User
from app.persistence.repository import list_usage_for_analytics

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
CurrentUser = Annotated[User, Depends(current_user)]


@router.get("/summary")
async def summary(user: CurrentUser, response: Response) -> dict:
    await check_rate_limit(str(user.id), "analytics", ANALYTICS_RATE_LIMIT_PER_HOUR)
    try:
        rows = await list_usage_for_analytics(user_id=user.id)
    except (SQLAlchemyError, PostgresError, OSError):
        raise HTTPException(503, "Usage storage unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return compute_summary(rows)
