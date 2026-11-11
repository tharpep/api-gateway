"""Webhook ingestion endpoint - normalizes external webhooks into internal events."""

import hashlib
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.http_client import get_client

logger = logging.getLogger(__name__)
router = APIRouter()
# GitHub can't send our API key, so this router is mounted without it: the signature is the auth.
github_router = APIRouter()


class WebhookPayload(BaseModel):
    source: str
    raw_payload: dict


class NormalizedEvent(BaseModel):
    event_type: str
    timestamp: str
    source: str
    data: dict


def _verify_github_signature(payload: bytes, signature_header: str | None) -> None:
    # Fail closed: with no secret configured, nothing can prove a request came from GitHub.
    if not settings.github_webhook_secret:
        raise HTTPException(503, "GitHub webhooks are off: GITHUB_WEBHOOK_SECRET is empty")
    if not signature_header:
        raise HTTPException(401, "Missing X-Hub-Signature-256 header")
    digest = hmac.new(settings.github_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(f"sha256={digest}", signature_header):
        raise HTTPException(401, "Invalid webhook signature")


@github_router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
):
    """Verify and forward. What an event starts is Sazed's decision (POST /events/...), not ours."""
    body_bytes = await request.body()
    _verify_github_signature(body_bytes, x_hub_signature_256)

    if x_github_event not in FORWARDED_EVENTS:
        return {"status": "ignored", "reason": f"unhandled event {x_github_event}"}

    sazed_url = settings.sazed_url.rstrip("/")
    if not sazed_url:
        raise HTTPException(503, "sazed_url is not configured")

    event = normalize_github(x_github_event, await request.json())
    headers = {"X-API-Key": settings.sazed_api_key} if settings.sazed_api_key else {}
    resp = await get_client().post(
        f"{sazed_url}/events/github", headers=headers, json=event, timeout=30.0
    )
    if not resp.is_success:
        logger.error("Sazed refused a %s event: %d %s", x_github_event, resp.status_code, resp.text)
        raise HTTPException(502, "Sazed couldn't take the event")
    return resp.json()


FORWARDED_EVENTS = (
    "pull_request",
    "issues",
    "issue_comment",
    "pull_request_review",
    "pull_request_review_comment",
)


def normalize_github(event: str, payload: dict) -> dict:
    """One flat shape for Sazed's intake (POST /events/github), whatever the GitHub event."""
    pr = payload.get("pull_request") or {}
    issue = payload.get("issue") or {}
    thread = pr or issue
    detail = payload.get("comment") or payload.get("review") or {}
    opened = event in ("pull_request", "issues")
    return {
        "event": event,
        "action": payload.get("action") or "",
        "repo": (payload.get("repository") or {}).get("full_name"),
        "number": thread.get("number"),
        "is_pr": bool(pr) or "pull_request" in issue,
        "sender": (payload.get("sender") or {}).get("login", ""),
        "title": (thread.get("title") or "")[:500],
        "body": ((thread if opened else detail).get("body") or "")[:8000],
        "branch": (pr.get("head") or {}).get("ref"),
        "draft": bool(pr.get("draft")),
        "merged": bool(pr.get("merged")),
        "url": ((thread if opened else detail).get("html_url") or "")[:500],
        "path": detail.get("path"),
        "line": detail.get("line") or detail.get("original_line"),
    }


@router.post("/ingest")
async def ingest_webhook(payload: WebhookPayload):
    raise HTTPException(
        status_code=501,
        detail=f"Not implemented — no handler configured for source '{payload.source}'.",
    )
