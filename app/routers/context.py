"""Context aggregation endpoint - unified snapshot for AI grounding."""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/now")
async def context_now():
    """Aggregated context snapshot from all services."""
    # TODO: Implement when individual router snapshots are ready
    raise HTTPException(
        status_code=501,
        detail="Not implemented — will aggregate /calendar/snapshot, /email/snapshot, etc.",
    )
