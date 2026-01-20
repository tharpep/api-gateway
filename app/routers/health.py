"""Health check and API directory endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Gateway status and available endpoints."""
    # TODO: Implement health check with endpoint directory
    return {"status": "ok"}
