"""Pushover notification endpoint."""

from enum import IntEnum

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings

router = APIRouter()

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


class Priority(IntEnum):
    LOWEST = -2      # No notification, just badge
    LOW = -1         # Quiet notification
    NORMAL = 0       # Normal notification
    HIGH = 1         # Bypass quiet hours
    EMERGENCY = 2    # Requires acknowledgment


class NotificationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=250)
    message: str = Field(..., min_length=1, max_length=1024)
    priority: Priority = Priority.NORMAL
    url: str | None = Field(None, max_length=512)
    url_title: str | None = Field(None, max_length=100)


class NotificationResponse(BaseModel):
    status: int
    request: str


@router.post("", response_model=NotificationResponse)
async def send_notification(notification: NotificationRequest):
    """Send notification via Pushover."""
    if not settings.pushover_user_key or not settings.pushover_api_token:
        raise HTTPException(
            status_code=503,
            detail="Pushover credentials not configured"
        )

    payload = {
        "token": settings.pushover_api_token,
        "user": settings.pushover_user_key,
        "title": notification.title,
        "message": notification.message,
        "priority": notification.priority,
    }

    if notification.url:
        payload["url"] = notification.url
    if notification.url_title:
        payload["url_title"] = notification.url_title

    # Emergency priority requires retry/expire params
    if notification.priority == Priority.EMERGENCY:
        payload["retry"] = 60       # Retry every 60 seconds
        payload["expire"] = 3600   

    async with httpx.AsyncClient() as client:
        response = await client.post(PUSHOVER_API_URL, data=payload)

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Pushover API error: {response.text}"
        )

    data = response.json()
    if data.get("status") != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Pushover rejected: {data.get('errors', 'Unknown error')}"
        )

    return NotificationResponse(status=data["status"], request=data["request"])
