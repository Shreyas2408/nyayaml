"""Analytics API routes — stub for Phase 0."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard():
    """Get analytics dashboard data — not yet implemented."""
    return JSONResponse(status_code=501, content={"detail": "Not implemented"})
