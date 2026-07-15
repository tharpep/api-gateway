"""API Gateway - FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.dependencies import verify_api_key
from app.http_client import shutdown as http_client_shutdown
from app.http_client import startup as http_client_startup
from app.migrations import run_migrations
from app.routers import (
    ai,
    calendar,
    context,
    email,
    finance,
    github,
    health,
    journal,
    kb,
    multi_search,
    notify,
    places,
    search,
    sheets,
    storage,
    tasks,
    webhooks,
)

logger = logging.getLogger(__name__)


def _configure_sentry() -> None:
    """No-op unless SENTRY_DSN is set — nothing to configure until a Sentry
    project exists. logger.error/exception calls are captured automatically
    via the logging integration once it is."""
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        integrations=[
            FastApiIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )
    logger.info("Sentry error tracking enabled")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _configure_sentry()
    await http_client_startup()

    # Run pending migrations before serving traffic. Failures crash startup so
    # Cloud Run keeps the previous revision live instead of running half-applied
    # schema.
    if settings.database_url:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        await run_migrations(dsn)
    else:
        logger.info("DATABASE_URL not set; skipping migrations")

    yield

    await http_client_shutdown()


app = FastAPI(
    title="API Gateway",
    description="Personal API gateway for centralized service access",
    version="0.1.0",
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = ai.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers (health is public; others require API key when API_KEY is set)
app.include_router(health.router)
app.include_router(
    notify.router, prefix="/notify", tags=["notify"], dependencies=[Depends(verify_api_key)]
)
app.include_router(ai.router, prefix="/ai", tags=["ai"], dependencies=[Depends(verify_api_key)])
app.include_router(
    calendar.router, prefix="/calendar", tags=["calendar"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    tasks.router, prefix="/tasks", tags=["tasks"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    email.router, prefix="/email", tags=["email"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    storage.router, prefix="/storage", tags=["storage"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    kb.router, prefix="/kb", tags=["kb"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    context.router, prefix="/context", tags=["context"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    search.router, prefix="/search", tags=["search"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    webhooks.router, prefix="/webhooks", tags=["webhooks"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    github.router, prefix="/github", tags=["github"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    sheets.router, prefix="/sheets", tags=["sheets"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    finance.router, prefix="/finance", tags=["finance"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    journal.router, prefix="/journal", tags=["journal"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    places.router, prefix="/places", tags=["places"], dependencies=[Depends(verify_api_key)]
)
app.include_router(
    multi_search.router, prefix="/multi-search", tags=["multi-search"],
    dependencies=[Depends(verify_api_key)],
)

