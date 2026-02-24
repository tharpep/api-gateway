"""Webhook ingestion endpoint - normalizes external webhooks into internal events."""

from fastapi import APIRouter
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


@router.post("/ingest", response_model=NormalizedEvent)
async def ingest_webhook(payload: WebhookPayload):
    """Ingest and normalize a webhook from an external source."""
    # TODO: Implement source-specific handlers
    return NormalizedEvent(
        event_type=f"{payload.source}.unknown",
        timestamp="",
        source=payload.source,
        data=payload.raw_payload,
    )
