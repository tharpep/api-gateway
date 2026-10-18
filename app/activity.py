"""In-memory feed of what the gateway did on its own, for Sazed's Activity page.

Sazed records its own work in Postgres; the gateway's (Pushovers sent, outages, disk and deploy
alerts, MCP tool calls) only lives here. Sazed polls `GET /activity/events?after=<seq>` every
few seconds and stores what's new. Kept in memory on purpose: a restart starts a new `boot`, and
Sazed simply picks up from there. No migration, so nothing to collide with on GCP's shared
migration counter.
"""

import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

BOOT = uuid.uuid4().hex[:12]
_events: deque[dict[str, Any]] = deque(maxlen=2000)
_seq = 0

router = APIRouter()


def record(
    kind: str,
    title: str,
    status: str = "info",
    detail: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> None:
    """kind: notify | outage | recovery | disk | deploy | mcp. status: ok | error | info."""
    global _seq
    _seq += 1
    _events.append(
        {
            "seq": _seq,
            "at": datetime.now(UTC).isoformat(),
            "kind": kind,
            "title": title[:300],
            "status": status,
            "detail": detail or {},
            "duration_ms": duration_ms,
        }
    )


@router.get("/events")
async def events(after: int = Query(0, ge=0), boot: str | None = Query(None)):
    """Events after `after`. If `boot` isn't this process's, the caller missed a restart and
    gets everything still buffered."""
    if boot != BOOT:
        after = 0
    return {"boot": BOOT, "events": [e for e in _events if e["seq"] > after]}
