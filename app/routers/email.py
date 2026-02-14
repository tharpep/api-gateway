"""Email endpoint - Gmail integration."""

from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth.google import GoogleOAuth, GMAIL_SCOPES, TokenData
from app.config import settings

router = APIRouter()

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"

_cached_token: TokenData | None = None
_oauth = GoogleOAuth(scopes=GMAIL_SCOPES)


async def _get_access_token() -> str:
    """Get a valid access token, refreshing if needed."""
    global _cached_token

    if _cached_token is None or _oauth.is_token_expired(_cached_token):
        if not settings.google_refresh_token:
            raise HTTPException(503, "Google refresh token not configured")
        _cached_token = await _oauth.refresh_token(settings.google_refresh_token)

    return _cached_token.access_token


class EmailMessage(BaseModel):
    id: str
    subject: str
    sender: str
    snippet: str
    date: str


class EmailResponse(BaseModel):
    messages: list[EmailMessage]
    count: int


async def _fetch_messages(hours: int = 24, max_results: int = 50) -> list[EmailMessage]:
    """Fetch messages from Gmail API."""
    access_token = await _get_access_token()

    query = f"category:primary newer_than:{hours}h"

    params = {
        "q": query,
        "maxResults": max_results,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GMAIL_API}/users/me/messages",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code == 401:
        global _cached_token
        _cached_token = None
        access_token = await _get_access_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{GMAIL_API}/users/me/messages",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )

    if response.status_code != 200:
        raise HTTPException(502, f"Gmail API error: {response.text}")

    data = response.json()
    message_ids = [msg["id"] for msg in data.get("messages", [])]

    if not message_ids:
        return []

    messages = []
    async with httpx.AsyncClient() as client:
        for msg_id in message_ids:
            msg_response = await client.get(
                f"{GMAIL_API}/users/me/messages/{msg_id}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if msg_response.status_code == 200:
                msg_data = msg_response.json()
                headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}

                messages.append(
                    EmailMessage(
                        id=msg_data["id"],
                        subject=headers.get("Subject", "(No subject)"),
                        sender=headers.get("From", "(Unknown sender)"),
                        snippet=msg_data.get("snippet", ""),
                        date=headers.get("Date", ""),
                    )
                )

    return messages


@router.get("/recent", response_model=EmailResponse)
async def get_recent(hours: int = Query(default=24, ge=1, le=168)):
    """Get recent email messages from primary inbox.

    Args:
        hours: Number of hours to look back (default: 24, max: 168/1 week)
    """
    messages = await _fetch_messages(hours=hours)
    return EmailResponse(messages=messages, count=len(messages))


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
