"""API Gateway - FastAPI application entry point."""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.dependencies import verify_api_key
from app.routers import ai, calendar, context, email, github, health, kb, notify, search, storage, tasks, webhooks

app = FastAPI(
    title="API Gateway",
    description="Personal API gateway for centralized service access",
    version="0.1.0",
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

