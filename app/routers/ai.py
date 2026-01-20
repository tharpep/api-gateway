"""AI API gateway (placeholder)."""

from fastapi import APIRouter

router = APIRouter()


@router.get("")
async def ai_status():
    """AI gateway status."""
    # TODO: Import existing implementation
    return {"status": "not implemented", "note": "AI gateway to be imported from existing codebase"}
