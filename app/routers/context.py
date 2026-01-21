"""Context aggregation endpoint - unified snapshot for AI grounding."""

from fastapi import APIRouter


router = APIRouter()


@router.get("/now")
async def context_now():
    """Aggregated context snapshot from all services."""
    # TODO: Implement when individual router snapshots are ready
    return {
        "status": "not implemented",
        "note": "Will aggregate /calendar/snapshot, /email/snapshot, etc.",
    }
