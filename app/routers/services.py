"""Service registry endpoints, mounted under /homelab next to the Proxmox routes.

Each service = its registry definition + the poller's latest in-memory result (`live`) +
its Postgres state (`state`). `state` is null when Postgres is unreachable; `live` still works.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import service_health
from app.registry import ServiceDef, get_service, load_registry

router = APIRouter()


def _live(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None
    return {
        "up": entry.get("up"),
        "detail": entry.get("detail"),
        "latency_ms": entry.get("latency_ms"),
        "checked_at": entry.get("checked_at"),
        "container_status": entry.get("container_status"),
    }


def _state(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    until = row.get("maintenance_until")
    return {
        "since": row.get("since"),
        "maintenance_until": until,
        "in_maintenance": bool(until and until > datetime.now(UTC)),
        "muted": row.get("muted", False),
        "notes": row.get("notes"),
    }


def _view(svc: ServiceDef, latest: dict, states: dict | None) -> dict[str, Any]:
    return {
        **svc.model_dump(),
        "live": _live(latest.get(svc.id)),
        "state": _state(states.get(svc.id)) if states is not None else None,
    }


def _compact(view: dict[str, Any]) -> dict[str, Any]:
    """The status-only shape for LLM callers (Sazed tools, MCP): full views carry env_keys and
    health config, which is ~4K tokens across the registry for a question like "is it all up?"."""
    live, state = view["live"] or {}, view["state"] or {}
    return {
        "id": view["id"],
        "name": view["name"],
        "group": view["group"],
        "container": view["container"],
        "lifecycle": view["lifecycle"],
        "up": live.get("up"),
        "detail": live.get("detail"),
        "since": state.get("since"),
        "in_maintenance": state.get("in_maintenance"),
        "muted": state.get("muted"),
        "notes": state.get("notes") or None,
    }


@router.get("/services")
async def list_services(
    group: str | None = Query(None, pattern="^(sazed|apps|infra)$"),
    compact: bool = False,
):
    """Every registry service with live health and console state, plus an up/down summary.
    `compact=true` returns status fields only (for LLM tools)."""
    latest, last_cycle, running = service_health.snapshot()
    states = await service_health.get_states()
    services = [s for s in load_registry() if group is None or s.group == group]
    views = [_view(s, latest, states) for s in services]
    checked = [v for v in views if v["lifecycle"] != "planned"]
    if compact:
        views = [_compact(v) for v in views]
    return {
        "poller_running": running,
        "last_cycle_at": last_cycle,
        "state_available": states is not None,
        "summary": {
            "up": sum(1 for v in checked if v["live"] and v["live"]["up"] is True),
            "down": sum(1 for v in checked if v["live"] and v["live"]["up"] is False),
            "unknown": sum(1 for v in checked if not v["live"] or v["live"]["up"] is None),
        },
        "services": views,
    }


@router.get("/services/{service_id}")
async def get_service_detail(service_id: str, event_limit: int = Query(50, ge=1, le=500)):
    """One service with its recent up/down transitions (outage history)."""
    svc = get_service(service_id)
    if svc is None:
        raise HTTPException(404, f"Unknown service '{service_id}'")
    latest, _, _ = service_health.snapshot()
    states = await service_health.get_states()
    return {
        **_view(svc, latest, states),
        "events": await service_health.get_events(service_id, event_limit),
    }


class ServiceStateUpdate(BaseModel):
    maintenance_minutes: int | None = Field(
        None, ge=0, le=7 * 24 * 60, description="Start a maintenance window; 0 ends it"
    )
    muted: bool | None = None
    notes: str | None = Field(None, max_length=2000)


@router.patch("/services/{service_id}/state")
async def update_service_state(service_id: str, body: ServiceStateUpdate):
    """Console-editable state: maintenance window, mute, notes. Omitted fields are unchanged."""
    if get_service(service_id) is None:
        raise HTTPException(404, f"Unknown service '{service_id}'")
    row = await service_health.set_state(
        service_id, body.maintenance_minutes, body.muted, body.notes
    )
    return {"id": service_id, "state": _state(row)}


@router.get("/map")
async def architecture_map():
    """The console's architecture map (registry/map.yaml) with live health per node, container
    stats, and outside-service status folded in."""
    from app import arch_map  # lazy: keeps router import light
    from app.routers import health, homelab

    latest, _, _ = service_health.snapshot()
    states = await service_health.get_states()
    try:
        containers = await homelab.list_containers()
    except Exception:  # Proxmox down or not configured: the map still renders, without stats
        containers = None
    integrations = (await health.get_integrations()).model_dump()
    try:
        return arch_map.build(latest, states, containers, integrations)
    except Exception as e:
        raise HTTPException(500, f"Map couldn't be built: {e}") from e
