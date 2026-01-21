from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])

_startup_time = datetime.now(timezone.utc)


class EndpointInfo(BaseModel):
    path: str
    description: str
    provider: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    endpoints: list[EndpointInfo]


ENDPOINTS = [
    EndpointInfo(path="/health", description="Gateway status and API directory"),
    EndpointInfo(path="/notify", description="Push notifications", provider="Pushover"),
    EndpointInfo(path="/ai", description="AI API gateway (OpenAI-compatible)", provider="Anthropic, OpenRouter"),
    EndpointInfo(path="/calendar", description="Calendar events", provider="Google Calendar"),
    EndpointInfo(path="/tasks", description="Task management", provider="Google Tasks"),
    EndpointInfo(path="/email", description="Email access", provider="Gmail"),
    EndpointInfo(path="/storage", description="File and photo storage", provider="Google Drive"),
]


@router.get("/health", response_model=HealthResponse)
async def health_check():
    uptime = (datetime.now(timezone.utc) - _startup_time).total_seconds()

    return HealthResponse(
        status="ok",
        version="0.1.0",
        uptime_seconds=round(uptime, 2),
        endpoints=ENDPOINTS,
    )
