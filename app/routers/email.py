"""Email endpoint - Gmail integration."""

import base64
from email.mime.text import MIMEText

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth.google import GMAIL_SCOPES, GoogleOAuth, TokenData
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


def _decode_body(payload: dict) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            result = _decode_body(part)
            if result:
                return result

    return ""


def _build_raw_message(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    """Build a base64url encoded raw MIME message for the Gmail API."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["to"] = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")



class EmailMessage(BaseModel):
    id: str
    subject: str
    sender: str
    snippet: str
    date: str


class EmailMessageDetail(EmailMessage):
    thread_id: str
    recipient: str
    body: str


class EmailResponse(BaseModel):
    messages: list[EmailMessage]
    count: int


class DraftRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: str | None = None



async def _fetch_messages(query: str, max_results: int = 50) -> list[EmailMessage]:
    """Fetch messages matching a Gmail query, returning metadata summaries."""
    access_token = await _get_access_token()

    params = {"q": query, "maxResults": max_results}

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
                hdrs = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
                messages.append(EmailMessage(
                    id=msg_data["id"],
                    subject=hdrs.get("Subject", "(No subject)"),
                    sender=hdrs.get("From", "(Unknown sender)"),
                    snippet=msg_data.get("snippet", ""),
                    date=hdrs.get("Date", ""),
                ))

    return messages



@router.get("", response_model=EmailResponse)
async def list_emails(
    unread_only: bool = Query(default=False),
    hours: int | None = Query(default=None, ge=1, le=168),
    max_results: int = Query(default=20, ge=1, le=50),
):
    """List emails from the primary inbox. Filter by unread status and/or recency."""
    query_parts = ["category:primary"]
    if unread_only:
        query_parts.append("is:unread")
    if hours is not None:
        query_parts.append(f"newer_than:{hours}h")
    messages = await _fetch_messages(" ".join(query_parts), max_results=max_results)
    return EmailResponse(messages=messages, count=len(messages))


@router.get("/recent", response_model=EmailResponse)
async def get_recent(hours: int = Query(default=24, ge=1, le=168)):
    """Get recent messages from primary inbox."""
    messages = await _fetch_messages(f"category:primary newer_than:{hours}h")
    return EmailResponse(messages=messages, count=len(messages))


@router.get("/unread", response_model=EmailResponse)
async def get_unread(max_results: int = Query(default=20, ge=1, le=50)):
    """Get unread messages from primary inbox."""
    messages = await _fetch_messages("is:unread category:primary", max_results=max_results)
    return EmailResponse(messages=messages, count=len(messages))


@router.get("/search", response_model=EmailResponse)
async def search_email(
    q: str = Query(description="Gmail search query, e.g. 'from:alice subject:meeting'"),
    max_results: int = Query(default=20, ge=1, le=50),
):
    """Search emails using Gmail query syntax."""
    messages = await _fetch_messages(q, max_results=max_results)
    return EmailResponse(messages=messages, count=len(messages))


@router.get("/messages/{message_id}", response_model=EmailMessageDetail)
async def get_message(message_id: str):
    """Get a specific message with full decoded body."""
    access_token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GMAIL_API}/users/me/messages/{message_id}",
            params={"format": "full"},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code == 404:
        raise HTTPException(404, "Message not found")
    if response.status_code != 200:
        raise HTTPException(502, f"Gmail API error: {response.text}")

    msg_data = response.json()
    hdrs = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
    body = _decode_body(msg_data.get("payload", {})) or msg_data.get("snippet", "")

    return EmailMessageDetail(
        id=msg_data["id"],
        thread_id=msg_data.get("threadId", ""),
        subject=hdrs.get("Subject", "(No subject)"),
        sender=hdrs.get("From", "(Unknown sender)"),
        recipient=hdrs.get("To", ""),
        snippet=msg_data.get("snippet", ""),
        date=hdrs.get("Date", ""),
        body=body,
    )


@router.post("/draft", status_code=201)
async def create_draft(body: DraftRequest):
    """Save an email as a draft."""
    access_token = await _get_access_token()

    raw = _build_raw_message(body.to, body.subject, body.body, body.cc)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{GMAIL_API}/users/me/drafts",
            json={"message": {"raw": raw}},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Gmail API error: {response.text}")

    data = response.json()
    return {"id": data.get("id"), "message_id": data.get("message", {}).get("id")}
