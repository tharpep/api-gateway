"""Webhook ingestion endpoint - normalizes external webhooks into internal events."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class WebhookPayload(BaseModel):
    source: str
    raw_payload: dict


class NormalizedEvent(BaseModel):
    event_type: str
    timestamp: str
    source: str
    data: dict


@router.post("/ingest")
async def ingest_webhook(payload: WebhookPayload):
    """Ingest and normalize a webhook from an external source."""
    # TODO: Implement source-specific handlers
    raise HTTPException(
        status_code=501,
        detail=f"Not implemented — no handler configured for source '{payload.source}'.",
    )
