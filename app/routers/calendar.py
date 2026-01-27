"""Calendar endpoint - Google Calendar initially, extensible to Outlook/Apple."""

from fastapi import APIRouter

router = APIRouter()



@router.get("/events")
async def get_events():
    """Get calendar events."""
    # TODO: Implement event retrieval
    return {"status": "not implemented"}


@router.get("/today")
async def get_today():
    """Get today's events."""
    # TODO: Implement today's events
    return {"status": "not implemented"}


@router.post("/events")
async def create_event():
    """Create a calendar event."""
    # TODO: Implement event creation
    return {"status": "not implemented"}
