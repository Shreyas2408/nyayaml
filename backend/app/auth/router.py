"""Auth API routes — stub for Phase 0."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/register")
async def register():
    """Register a new user — not yet implemented."""
    return {"detail": "Not implemented"}, 501


@router.post("/login")
async def login():
    """Authenticate a user — not yet implemented."""
    return {"detail": "Not implemented"}, 501


@router.post("/refresh")
async def refresh_token():
    """Refresh access token — not yet implemented."""
    return {"detail": "Not implemented"}, 501
