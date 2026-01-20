"""API Gateway - FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import health, notify, ai, finance, google

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
app.include_router(finance.router, prefix="/finance", tags=["finance"])
app.include_router(google.router, prefix="/google", tags=["google"])
