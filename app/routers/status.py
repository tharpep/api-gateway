"""Integration status endpoint - tracks what's connected and healthy."""

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings


router = APIRouter()


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


@router.get("/integrations", response_model=IntegrationsResponse)
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


@router.get("/integrations/{name}", response_model=IntegrationStatus)
async def get_integration_status(name: str):
    all_integrations = await get_integrations()
    status = getattr(all_integrations, name, None)

    if status is None:
        return IntegrationStatus(connected=False, status=f"unknown integration: {name}")

    return status
