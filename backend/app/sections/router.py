"""Sections API routes — stub for Phase 0."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/")
async def list_sections():
    """List legal sections — not yet implemented."""
    return JSONResponse(status_code=501, content={"detail": "Not implemented"})


@router.get("/{section_id}")
async def get_section(section_id: str):
    """Get a specific legal section — not yet implemented."""
    return JSONResponse(status_code=501, content={"detail": "Not implemented"})
