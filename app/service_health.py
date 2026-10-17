"""Health poller for the service registry, plus the Postgres-backed service state.

Every `health_poll_seconds` the poller checks each non-planned registry entry from CT 104 and
keeps the latest results in memory, so GET /homelab/services keeps working when Postgres is
down (Postgres is one of the things being watched). Postgres holds what must survive restarts:
when the status last changed, up/down transitions (outage history), and the console-editable
state (maintenance window, mute, notes).

A service is marked down only after two failed checks in a row, so one slow response doesn't
log an outage; one success marks it up again.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
from fastapi import HTTPException

from app.config import settings
from app.http_client import get_client
from app.registry import ServiceDef, load_registry
from app.routers import homelab

logger = logging.getLogger(__name__)

_FAILS_TO_MARK_DOWN = 2
_HTTP_TIMEOUT = 5.0
_TCP_TIMEOUT = 3.0

# service_id -> latest in-memory result (see _record)
_latest: dict[str, dict[str, Any]] = {}
_last_cycle_at: datetime | None = None
_pool: asyncpg.Pool | None = None
_task: asyncio.Task | None = None


# ---------------------------------------------------------------- database


async def _get_pool() -> asyncpg.Pool | None:
    global _pool
    if _pool is None and settings.database_url:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
    return _pool


async def get_states() -> dict[str, dict[str, Any]] | None:
    """All service_state rows by id, or None if Postgres is unavailable."""
    try:
        pool = await _get_pool()
        if pool is None:
            return None
        rows = await pool.fetch("SELECT * FROM service_state")
    except Exception:
        logger.warning("service_state read failed", exc_info=True)
        return None
    return {r["service_id"]: dict(r) for r in rows}


async def get_events(service_id: str, limit: int = 50) -> list[dict[str, Any]] | None:
    try:
        pool = await _get_pool()
        if pool is None:
            return None
        rows = await pool.fetch(
            "SELECT at, up, detail, in_maintenance FROM service_events "
            "WHERE service_id = $1 ORDER BY at DESC LIMIT $2",
            service_id,
            limit,
        )
    except Exception:
        logger.warning("service_events read failed", exc_info=True)
        return None
    return [dict(r) for r in rows]


async def set_state(
    service_id: str,
    maintenance_minutes: int | None,
    muted: bool | None,
    notes: str | None,
) -> dict[str, Any]:
    """Update console-editable state. maintenance_minutes=0 ends a maintenance window."""
    pool = await _get_pool()
    if pool is None:
        raise HTTPException(503, "Database not configured (DATABASE_URL)")
    maintenance_until = None
    if maintenance_minutes:
        maintenance_until = datetime.now(UTC) + timedelta(minutes=maintenance_minutes)
    row = await pool.fetchrow(
        """
        INSERT INTO service_state (service_id, maintenance_until, muted, notes)
        VALUES ($1, $2, COALESCE($3, FALSE), $4)
        ON CONFLICT (service_id) DO UPDATE SET
            maintenance_until = CASE WHEN $5 THEN EXCLUDED.maintenance_until
                                     ELSE service_state.maintenance_until END,
            muted = COALESCE($3, service_state.muted),
            notes = CASE WHEN $6 THEN $4 ELSE service_state.notes END,
            updated_at = NOW()
        RETURNING *
        """,
        service_id,
        maintenance_until,
        muted,
        notes,
        maintenance_minutes is not None,
        notes is not None,
    )
    return dict(row)


# ---------------------------------------------------------------- checks


async def _check_http(url: str) -> tuple[bool, str]:
    try:
        r = await get_client().get(url, timeout=_HTTP_TIMEOUT, follow_redirects=False)
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.HTTPError as e:
        return False, type(e).__name__
    return r.status_code < 400, f"HTTP {r.status_code}"


async def _check_tcp(host: str, port: int) -> tuple[bool, str]:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), _TCP_TIMEOUT)
    except (TimeoutError, OSError) as e:
        return False, type(e).__name__
    writer.close()
    return True, "port open"


async def _check(svc: ServiceDef, containers: dict[int, str] | None, host_ok: bool):
    """Returns (ok, detail, latency_ms). ok=None means unknown (Proxmox unreachable)."""
    started = time.perf_counter()
    check = svc.health
    container_status = containers.get(svc.container) if containers and svc.container else None

    if check.type == "proxmox_host":
        ok, detail = host_ok, "reachable" if host_ok else "Proxmox API unreachable"
    elif check.type == "container":
        if containers is None:
            return None, "Proxmox API unreachable", None
        ok = container_status == "running"
        detail = f"container {container_status or 'missing'}"
    else:
        if check.type == "http":
            ok, detail = await _check_http(check.url)
        else:
            ok, detail = await _check_tcp(check.host, check.port)
        # The app check alone can't tell "app crashed" from "container stopped".
        if not ok and container_status and container_status != "running":
            detail = f"container {container_status}"

    return ok, detail, int((time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------- poll cycle


async def _proxmox_snapshot() -> tuple[dict[int, str] | None, bool]:
    try:
        containers = await homelab.list_containers()
    except HTTPException:
        return None, False
    return {c["vmid"]: c["status"] for c in containers}, True


def _record(svc: ServiceDef, ok: bool | None, detail: str, latency_ms: int | None):
    """Fold one raw check into the confirmed status. Returns (previous_up, new_up)."""
    entry = _latest.setdefault(svc.id, {"up": None, "fail_streak": 0, "seeded": False})
    previous = entry["up"]
    if ok is True:
        entry["fail_streak"] = 0
        entry["up"] = True
    elif ok is False:
        entry["fail_streak"] += 1
        if entry["fail_streak"] >= _FAILS_TO_MARK_DOWN or previous is None:
            entry["up"] = False
    entry.update(
        raw_ok=ok,
        detail=detail,
        latency_ms=latency_ms,
        checked_at=datetime.now(UTC),
    )
    return previous, entry["up"]


async def _persist(changes: list[tuple[ServiceDef, bool | None, bool | None]]) -> None:
    pool = await _get_pool()
    if pool is None:
        return
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        existing = {
            r["service_id"]: r
            for r in await conn.fetch(
                "SELECT service_id, up, since, maintenance_until FROM service_state"
            )
        }
        for svc, previous, current in changes:
            entry = _latest[svc.id]
            row = existing.get(svc.id)
            # After a restart, memory starts empty: take the last known status from the DB
            # so the first cycle doesn't log a fake transition.
            if not entry["seeded"]:
                entry["seeded"] = True
                if row is not None and previous is None:
                    previous = row["up"]
            changed = current is not None and current != previous
            since = now if changed or row is None or row["since"] is None else row["since"]
            await conn.execute(
                """
                INSERT INTO service_state
                    (service_id, up, since, last_checked, last_latency_ms, last_detail)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (service_id) DO UPDATE SET
                    up = EXCLUDED.up, since = EXCLUDED.since,
                    last_checked = EXCLUDED.last_checked,
                    last_latency_ms = EXCLUDED.last_latency_ms,
                    last_detail = EXCLUDED.last_detail, updated_at = NOW()
                """,
                svc.id,
                current,
                since,
                now,
                entry["latency_ms"],
                entry["detail"],
            )
            if changed and previous is not None:
                in_maintenance = bool(
                    row and row["maintenance_until"] and row["maintenance_until"] > now
                )
                await conn.execute(
                    "INSERT INTO service_events (service_id, up, detail, in_maintenance) "
                    "VALUES ($1, $2, $3, $4)",
                    svc.id,
                    current,
                    entry["detail"],
                    in_maintenance,
                )
                logger.info(
                    "Service %s is now %s (%s)",
                    svc.id,
                    "UP" if current else "DOWN",
                    entry["detail"],
                )


async def poll_once() -> None:
    global _last_cycle_at
    services = [s for s in load_registry() if s.lifecycle != "planned" and s.health]
    containers, host_ok = await _proxmox_snapshot()
    results = await asyncio.gather(*(_check(s, containers, host_ok) for s in services))
    changes = []
    for svc, (ok, detail, latency_ms) in zip(services, results):
        previous, current = _record(svc, ok, detail, latency_ms)
        if svc.container and containers is not None:
            _latest[svc.id]["container_status"] = containers.get(svc.container, "missing")
        changes.append((svc, previous, current))
    _last_cycle_at = datetime.now(UTC)
    try:
        await _persist(changes)
    except Exception:
        logger.warning("Service state persist failed (results kept in memory)", exc_info=True)


async def _run_forever() -> None:
    while True:
        started = time.monotonic()
        try:
            await poll_once()
        except Exception:
            logger.exception("Health poll cycle failed")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(5.0, settings.health_poll_seconds - elapsed))


def start() -> None:
    global _task
    if not settings.proxmox_host:
        logger.info("Health poller disabled (PROXMOX_HOST not set)")
        return
    _task = asyncio.create_task(_run_forever())
    logger.info("Health poller started (every %ss)", settings.health_poll_seconds)


async def stop() -> None:
    if _task is not None:
        _task.cancel()
    if _pool is not None:
        await _pool.close()


def snapshot() -> tuple[dict[str, dict[str, Any]], datetime | None, bool]:
    """(latest results by id, last cycle time, poller running)."""
    return _latest, _last_cycle_at, _task is not None and not _task.done()
