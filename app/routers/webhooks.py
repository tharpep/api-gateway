"""Webhook ingestion endpoint - normalizes external webhooks into internal events."""

import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.dependencies import verify_api_key
from app.http_client import get_client

logger = logging.getLogger(__name__)
router = APIRouter()


class WebhookPayload(BaseModel):
    source: str
    raw_payload: dict


class NormalizedEvent(BaseModel):
    event_type: str
    timestamp: str
    source: str
    data: dict


def _verify_github_signature(payload: bytes, signature_header: str | None) -> None:
    if not settings.github_webhook_secret:
        return
    if not signature_header:
        raise HTTPException(401, "Missing X-Hub-Signature-256 header")
    digest = hmac.new(settings.github_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(401, "Invalid webhook signature")


@router.post("/github")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
):
    body_bytes = await request.body()
    _verify_github_signature(body_bytes, x_hub_signature_256)

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"unhandled event {x_github_event}"}

    payload = await request.json()
    action = payload.get("action")
    if action not in ("opened", "synchronize"):
        return {"status": "ignored", "reason": f"unhandled pull_request action {action}"}

    pr = payload.get("pull_request") or {}
    if pr.get("draft"):
        return {"status": "ignored", "reason": "PR is a draft"}

    sender = (payload.get("sender") or {}).get("login", "")
    if sender.endswith("[bot]"):
        return {"status": "ignored", "reason": "ignoring bot PR"}

    repo = (payload.get("repository") or {}).get("full_name")
    pr_number = pr.get("number")
    branch = (pr.get("head") or {}).get("ref")
    title = pr.get("title", "")
    body = pr.get("body", "")

    sazed_url = settings.sazed_url.rstrip("/")
    if not sazed_url:
        raise HTTPException(503, "sazed_url is not configured")

    headers = {"X-API-Key": settings.sazed_api_key} if settings.sazed_api_key else {}
    task_payload = {
        "repo": repo,
        "instruction": f"Review PR #{pr_number}: {title}\n\n{body}",
        "mode": "review",
        "agent": "agy",
        "model": "gemini-3.8-flash-high",
        "requested_by": "webhook",
        "pr_number": pr_number,
        "branch": branch,
    }
    resp = await get_client().post(
        f"{sazed_url}/jobs/code-task",
        headers=headers,
        json=task_payload,
        timeout=30.0,
    )
    if not resp.is_success:
        logger.error("Failed to start review job in sazed: %d %s", resp.status_code, resp.text)
        raise HTTPException(resp.status_code, f"Failed to start review in sazed: {resp.text}")
    return {"status": "queued", "job": resp.json()}


@router.post("/ingest", dependencies=[Depends(verify_api_key)])
async def ingest_webhook(payload: WebhookPayload):
    raise HTTPException(
        status_code=501,
        detail=f"Not implemented — no handler configured for source '{payload.source}'.",
    )
