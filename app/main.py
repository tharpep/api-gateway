"""API Gateway - FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import health, notify, ai, calendar, tasks, email, storage, status, context, webhooks

app = FastAPI(
    title="API Gateway",
    description="Personal API gateway for centralized service access",
    version="0.1.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(health.router)
app.include_router(notify.router, prefix="/notify", tags=["notify"])
app.include_router(ai.router, prefix="/ai", tags=["ai"])
app.include_router(calendar.router, prefix="/calendar", tags=["calendar"])
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(email.router, prefix="/email", tags=["email"])
app.include_router(storage.router, prefix="/storage", tags=["storage"])
app.include_router(status.router, prefix="/status", tags=["status"])
app.include_router(context.router, prefix="/context", tags=["context"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
