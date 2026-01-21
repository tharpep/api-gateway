from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings


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


class IntegrationStatus(BaseModel):
    connected: bool
    status: str
    last_check: str | None = None


class IntegrationsResponse(BaseModel):
    google_calendar: IntegrationStatus
    google_email: IntegrationStatus
    google_tasks: IntegrationStatus
    google_storage: IntegrationStatus
    pushover: IntegrationStatus
    anthropic: IntegrationStatus
    openrouter: IntegrationStatus


ENDPOINTS = [
    EndpointInfo(path="/health", description="Gateway status and API directory"),
    EndpointInfo(path="/health/integrations", description="Integration connection status"),
    EndpointInfo(path="/notify", description="Push notifications", provider="Pushover"),
    EndpointInfo(path="/ai", description="AI API gateway (OpenAI-compatible)", provider="Anthropic, OpenRouter"),
    EndpointInfo(path="/calendar", description="Calendar events", provider="Google Calendar"),
    EndpointInfo(path="/tasks", description="Task management", provider="Google Tasks"),
    EndpointInfo(path="/email", description="Email access", provider="Gmail"),
    EndpointInfo(path="/storage", description="File and photo storage", provider="Google Drive"),
    EndpointInfo(path="/context", description="Aggregated context snapshot"),
    EndpointInfo(path="/webhooks", description="Webhook ingestion and normalization"),
]


def _check_google_services() -> IntegrationStatus:
    has_creds = bool(settings.google_client_id and settings.google_client_secret)
    has_token = bool(settings.google_refresh_token)

    if not has_creds:
        return IntegrationStatus(connected=False, status="credentials not configured")
    if not has_token:
        return IntegrationStatus(connected=False, status="not authenticated (no refresh token)")

    return IntegrationStatus(
        connected=True,
        status="ok",
        last_check=datetime.now(timezone.utc).isoformat(),
    )


def _check_pushover() -> IntegrationStatus:
    if not settings.pushover_user_key or not settings.pushover_api_token:
        return IntegrationStatus(connected=False, status="credentials not configured")
    return IntegrationStatus(
        connected=True,
        status="ok",
        last_check=datetime.now(timezone.utc).isoformat(),
    )


def _check_anthropic() -> IntegrationStatus:
    if not settings.anthropic_api_key:
        return IntegrationStatus(connected=False, status="api key not configured")
    return IntegrationStatus(
        connected=True,
        status="ok",
        last_check=datetime.now(timezone.utc).isoformat(),
    )


def _check_openrouter() -> IntegrationStatus:
    if not settings.openrouter_api_key:
        return IntegrationStatus(connected=False, status="api key not configured")
    return IntegrationStatus(
        connected=True,
        status="ok",
        last_check=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/health", response_model=HealthResponse)
async def health_check():
    uptime = (datetime.now(timezone.utc) - _startup_time).total_seconds()

    return HealthResponse(
        status="ok",
        version="0.1.0",
        uptime_seconds=round(uptime, 2),
        endpoints=ENDPOINTS,
    )


@router.get("/health/integrations", response_model=IntegrationsResponse)
async def get_integrations():
    google_status = _check_google_services()

    return IntegrationsResponse(
        google_calendar=google_status,
        google_email=google_status,
        google_tasks=google_status,
        google_storage=google_status,
        pushover=_check_pushover(),
        anthropic=_check_anthropic(),
        openrouter=_check_openrouter(),
    )
