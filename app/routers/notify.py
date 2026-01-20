"""Pushover notification endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.post("")
async def send_notification():
    """Send notification via Pushover."""
    # TODO: Implement Pushover integration
    return {"status": "not implemented"}
