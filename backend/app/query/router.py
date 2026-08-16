"""Query API routes — stub for Phase 0."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/")
async def submit_query():
    """Submit a legal query — not yet implemented."""
    return JSONResponse(status_code=501, content={"detail": "Not implemented"})
