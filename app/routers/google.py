"""Google services gateway."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/auth")
async def initiate_oauth():
    """Initiate Google OAuth flow."""
    # TODO: Implement OAuth initiation
    return {"status": "not implemented"}


@router.get("/callback")
async def oauth_callback():
    """Handle OAuth callback."""
    # TODO: Implement OAuth callback
    return {"status": "not implemented"}


# Sub-routers for individual services will be added:
# - /google/calendar/*
# - /google/tasks/*
# - /google/gmail/*
# - /google/drive/*
# - /google/photos/*
