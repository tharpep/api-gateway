"""Email endpoint - Gmail integration."""

import base64
from email.mime.text import MIMEText

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth import token_manager
from app.errors import parse_google_error
from app.http_client import get_client

router = APIRouter()

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


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


class SendRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: str | None = None


class ReplyRequest(BaseModel):
    body: str
    reply_all: bool = False  # also CC everyone the original message was CC'd to



async def _fetch_messages(query: str, max_results: int = 50) -> list[EmailMessage]:
    """Fetch messages matching a Gmail query, returning metadata summaries."""
    params = {"q": query, "maxResults": max_results}
    client = get_client()

    response = await token_manager.google_request(
        client, "GET", f"{GMAIL_API}/users/me/messages", params=params
    )

    if response.status_code != 200:
        raise HTTPException(502, f"Gmail API error: {parse_google_error(response.text)}")

    data = response.json()
    message_ids = [msg["id"] for msg in data.get("messages", [])]

    if not message_ids:
        return []

    messages = []
    for msg_id in message_ids:
        msg_response = await token_manager.google_request(
            client,
            "GET",
            f"{GMAIL_API}/users/me/messages/{msg_id}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
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
    response = await token_manager.google_request(
        get_client(),
        "GET",
        f"{GMAIL_API}/users/me/messages/{message_id}",
        params={"format": "full"},
    )

    if response.status_code == 404:
        raise HTTPException(404, "Message not found")
    if response.status_code != 200:
        raise HTTPException(502, f"Gmail API error: {parse_google_error(response.text)}")

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
    raw = _build_raw_message(body.to, body.subject, body.body, body.cc)

    response = await token_manager.google_request(
        get_client(), "POST", f"{GMAIL_API}/users/me/drafts", json={"message": {"raw": raw}}
    )

    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Gmail API error: {parse_google_error(response.text)}")

    data = response.json()
    return {"id": data.get("id"), "message_id": data.get("message", {}).get("id")}


@router.post("/send", status_code=201)
async def send_email(body: SendRequest):
    """Send an email immediately — no draft step.

    Uses the gmail.compose scope, already granted (that scope covers both
    draft management and sending messages/drafts), so this needs no new
    OAuth consent.
    """
    raw = _build_raw_message(body.to, body.subject, body.body, body.cc)

    response = await token_manager.google_request(
        get_client(), "POST", f"{GMAIL_API}/users/me/messages/send", json={"raw": raw}
    )

    if response.status_code not in (200, 201):
        raise HTTPException(502, f"Gmail API error: {parse_google_error(response.text)}")

    data = response.json()
    return {"id": data.get("id"), "thread_id": data.get("threadId")}


@router.post("/reply/{message_id}", status_code=201)
async def reply_to_email(message_id: str, body: ReplyRequest):
    """Reply to an existing message, keeping it in the same Gmail thread.

    Replies to the original sender (From header). reply_all also CCs
    whoever the original message was CC'd to — it does not merge the
    original To recipients into CC.
    """
    client = get_client()

    orig_resp = await token_manager.google_request(
        client,
        "GET",
        f"{GMAIL_API}/users/me/messages/{message_id}",
        params={
            "format": "metadata",
            "metadataHeaders": ["From", "Cc", "Subject", "Message-ID", "References"],
        },
    )
    if orig_resp.status_code == 404:
        raise HTTPException(404, "Message not found")
    if orig_resp.status_code != 200:
        raise HTTPException(502, f"Gmail API error: {parse_google_error(orig_resp.text)}")

    orig = orig_resp.json()
    hdrs = {h["name"]: h["value"] for h in orig.get("payload", {}).get("headers", [])}
    thread_id = orig.get("threadId", "")

    to = hdrs.get("From", "")
    if not to:
        raise HTTPException(422, "Original message has no From address to reply to")

    subject = hdrs.get("Subject", "")
    if subject and not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    cc = hdrs.get("Cc") if body.reply_all else None

    orig_message_id = hdrs.get("Message-ID", "")
    references = " ".join(filter(None, [hdrs.get("References", ""), orig_message_id])).strip()

    raw = _build_raw_message(
        to,
        subject,
        body.body,
        cc=cc,
        in_reply_to=orig_message_id or None,
        references=references or None,
    )

    send_response = await token_manager.google_request(
        client,
        "POST",
        f"{GMAIL_API}/users/me/messages/send",
        json={"raw": raw, "threadId": thread_id},
    )
    if send_response.status_code not in (200, 201):
        raise HTTPException(502, f"Gmail API error: {parse_google_error(send_response.text)}")

    data = send_response.json()
    return {"id": data.get("id"), "thread_id": data.get("threadId")}
