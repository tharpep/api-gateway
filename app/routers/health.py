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
    proxmox: IntegrationStatus
    tavily: IntegrationStatus
    github: IntegrationStatus
    gnews: IntegrationStatus
    google_maps: IntegrationStatus
    knowledge_base: IntegrationStatus
    database: IntegrationStatus


# The gateway's capability directory (the console's Gateway page lists it). Keep in step with
# the routers mounted in app/main.py.
ENDPOINTS = [
    EndpointInfo(path="/health", description="Gateway status and API directory"),
    EndpointInfo(path="/health/integrations", description="Integration connection status"),
    EndpointInfo(path="/calendar", description="Calendar events", provider="Google Calendar"),
    EndpointInfo(path="/tasks", description="Task lists and tasks", provider="Google Tasks"),
    EndpointInfo(path="/email", description="Read, search and draft email", provider="Gmail"),
    EndpointInfo(path="/storage", description="Drive files and folders", provider="Google Drive"),
    EndpointInfo(path="/sheets", description="Spreadsheets", provider="Google Sheets"),
    EndpointInfo(path="/places", description="Place search and details", provider="Google Maps"),
    EndpointInfo(path="/search", description="Web search and page fetch", provider="Tavily"),
    EndpointInfo(
        path="/multi-search",
        description="News and social search with bias signals",
        provider="GNews, Reddit, HN, Bluesky, RSS",
    ),
    EndpointInfo(path="/github", description="Repos, issues, PRs, code", provider="GitHub"),
    EndpointInfo(path="/kb", description="Knowledge base search, uploads and sources", provider="KB service"),
    EndpointInfo(path="/journal", description="Work contribution journal", provider="Postgres"),
    EndpointInfo(path="/finance", description="Subscriptions, budget, bills", provider="Postgres"),
    EndpointInfo(path="/notify", description="Push notifications", provider="Pushover"),
    EndpointInfo(
        path="/ai", description="LLM proxy (OpenAI-compatible)", provider="Anthropic, OpenRouter"
    ),
    EndpointInfo(
        path="/homelab", description="Host, containers, service health", provider="Proxmox"
    ),
    EndpointInfo(path="/mcp", description="MCP tools for Claude and agy"),
    EndpointInfo(path="/context", description="Aggregated context snapshot (stub)"),
    EndpointInfo(path="/webhooks", description="Webhook ingestion (stub)"),
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


def _check_proxmox() -> IntegrationStatus:
    if not settings.proxmox_host or not settings.proxmox_read_token:
        return IntegrationStatus(connected=False, status="host or read token not configured")
    if not settings.proxmox_power_token:
        return IntegrationStatus(
            connected=True,
            status="read-only (no power token)",
            last_check=datetime.now(timezone.utc).isoformat(),
        )
    return IntegrationStatus(
        connected=True,
        status="ok",
        last_check=datetime.now(timezone.utc).isoformat(),
    )


def _check_configured(configured: bool, missing: str) -> IntegrationStatus:
    """Config-presence check for integrations with no richer probe (same as the ones above)."""
    if not configured:
        return IntegrationStatus(connected=False, status=missing)
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
        proxmox=_check_proxmox(),
        tavily=_check_configured(bool(settings.tavily_api_key), "api key not configured"),
        github=_check_configured(bool(settings.github_token), "token not configured"),
        gnews=_check_configured(bool(settings.gnews_api_key), "api key not configured"),
        google_maps=_check_configured(
            bool(settings.google_maps_api_key), "api key not configured"
        ),
        knowledge_base=_check_configured(
            bool(settings.kb_service_url), "KB_SERVICE_URL not configured"
        ),
        database=_check_configured(bool(settings.database_url), "DATABASE_URL not configured"),
    )
