"""Email endpoint - Gmail initially, extensible to Outlook/SMTP."""

from fastapi import APIRouter

router = APIRouter()



@router.get("/unread")
async def get_unread():
    """Get unread email count and summaries."""
    # TODO: Implement unread retrieval
    return {"status": "not implemented"}


@router.get("/messages")
async def get_messages():
    """Get recent messages."""
    # TODO: Implement message retrieval
    return {"status": "not implemented"}


@router.get("/messages/{message_id}")
async def get_message(message_id: str):
    """Get a specific message."""
    # TODO: Implement single message retrieval
    return {"status": "not implemented"}


@router.post("/send")
async def send_email():
    """Send an email."""
    # TODO: Implement email sending
    return {"status": "not implemented"}
