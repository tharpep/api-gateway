"""Deterministic Pushover alerts, driven by the health poller (app/service_health.py).

No LLM involved: these fire on facts the poller already has.

- Outages: a registry service is confirmed down (two failed checks) or recovers. One message per
  poll cycle lists everything that changed, so a stopped container with ten apps on it sends one
  alert, not ten. Muted services and services in a maintenance window are skipped.
- Disk: a Proxmox storage pool reaches alert_pool_percent, or a running container's root disk
  reaches alert_disk_percent. Pools go first because a full thin pool fails writes in every
  container on it. Each re-arms once usage drops 5 points below, so a disk sitting at 86%
  alerts once.
- Deploys: a "Deploy to Homelab" workflow run fails for any active registry repo.

The whole homelab being down can't be reported from inside it; that needs an outside dead-man's
switch (e.g. healthchecks.io pinged by the poller), which is not set up.

Dedupe state is in memory. Outage state survives restarts via service_state (the poller seeds
from it); disk and deploy state don't, so a gateway restart can repeat a still-true disk alert
once, and deploy failures are only reported for runs that finish after the process started.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from app import activity
from app.config import settings
from app.http_client import get_client
from app.registry import ServiceDef, load_registry
from app.routers import homelab
from app.routers.github import GITHUB_API
from app.routers.github import _headers as _github_headers
from app.routers.notify import Priority, send_pushover

logger = logging.getLogger(__name__)

_DEPLOY_WORKFLOW = "Deploy to Homelab"
_FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}
_DISK_REARM_POINTS = 5

_started_at = datetime.now(UTC)
_disk_firing: set[str] = set()
_deploys_alerted: set[int] = set()
_last_deploy_check: datetime | None = None


async def _push(title: str, message: str, priority: Priority, url: str | None = None) -> None:
    if not settings.alerts_enabled:
        return
    try:
        await send_pushover(title, message, priority, url or settings.console_url)
    except HTTPException as e:
        logger.warning("Alert not delivered (%s): %s", e.detail, title)
    except Exception:
        logger.warning("Alert not delivered: %s", title, exc_info=True)


def _quiet(service_id: str, states: dict[str, dict[str, Any]] | None, now: datetime) -> bool:
    """Muted, or inside a maintenance window. Unknown state (Postgres down) alerts anyway."""
    row = (states or {}).get(service_id)
    if not row:
        return False
    until = row.get("maintenance_until")
    return bool(row.get("muted")) or bool(until and until > now)


async def outages(
    transitions: list[tuple[ServiceDef, bool, str]],
    states: dict[str, dict[str, Any]] | None,
) -> None:
    """transitions: (service, now_up, detail) for every confirmed change this cycle."""
    now = datetime.now(UTC)
    loud = [(s, up, d) for s, up, d in transitions if not _quiet(s.id, states, now)]
    down = [(s, d) for s, up, d in loud if not up]
    back = [s for s, up, _ in loud if up]

    if down:
        lines = [f"{s.name}: {d}" for s, d in down]
        # Sazed and infrastructure break other things; media apps can wait for the morning.
        urgent = any(s.group != "apps" for s, _ in down)
        title = f"Down: {down[0][0].name}" if len(down) == 1 else f"{len(down)} services down"
        await _push(title, "\n".join(lines), Priority.HIGH if urgent else Priority.NORMAL)
    if back:
        names = ", ".join(s.name for s in back)
        await _push(f"Recovered: {names}"[:250], f"Back up: {names}", Priority.LOW)


async def disks(containers: list[dict] | None) -> None:
    """Check storage pools (alert_pool_percent) and running containers' root disks
    (alert_disk_percent) against their thresholds."""
    # key -> (label, fraction used, threshold percent, free bytes)
    usage: dict[str, tuple[str, float, int, int]] = {}
    try:
        storages = await homelab._pve("GET", homelab._node("/storage"), settings.proxmox_read_token)
    except HTTPException:
        storages = None
    for s in storages or []:
        if s.get("active") and s.get("total"):
            free = s.get("avail", s["total"] - s["used"])
            usage[f"storage:{s['storage']}"] = (
                f"Storage {s['storage']}",
                s["used"] / s["total"],
                settings.alert_pool_percent,
                free,
            )
    for c in containers or []:
        if c.get("status") == "running" and c.get("disk_total_bytes"):
            used = c.get("disk_used_bytes") or 0
            usage[f"ct:{c['vmid']}"] = (
                f"CT {c['vmid']} {c.get('name') or ''} disk".strip(),
                used / c["disk_total_bytes"],
                settings.alert_disk_percent,
                c["disk_total_bytes"] - used,
            )

    newly = []
    for key, (label, pct, threshold, free) in usage.items():
        if pct >= threshold / 100 and key not in _disk_firing:
            _disk_firing.add(key)
            newly.append(f"{label}: {pct:.0%} used, {free / 1024**3:.1f} GB free")
        elif pct < (threshold - _DISK_REARM_POINTS) / 100:
            _disk_firing.discard(key)
    if newly:
        title = "Disk space low"
        activity.record("disk", title, "error", {"lines": newly})
        await _push(title, "\n".join(newly), Priority.NORMAL)


async def deploys() -> None:
    """Every alert_deploy_poll_seconds: report failed homelab deploy runs not seen before."""
    global _last_deploy_check
    now = datetime.now(UTC)
    if not settings.github_token:
        return
    if _last_deploy_check and now - _last_deploy_check < timedelta(
        seconds=settings.alert_deploy_poll_seconds
    ):
        return
    _last_deploy_check = now

    repos = sorted({s.repo for s in load_registry() if s.repo and s.lifecycle == "active"})
    failed = []
    for repo in repos:
        try:
            r = await get_client().get(
                f"{GITHUB_API}/repos/{repo}/actions/runs",
                params={"status": "completed", "per_page": 10},
                headers=_github_headers(),
                timeout=20.0,
            )
            r.raise_for_status()
        except Exception as e:
            logger.warning("Deploy check failed for %s: %s", repo, e)
            continue
        for run in r.json().get("workflow_runs", []):
            finished = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
            if (
                run.get("name") == _DEPLOY_WORKFLOW
                and run.get("conclusion") in _FAILED_CONCLUSIONS
                and finished > _started_at - timedelta(minutes=2)
                and run["id"] not in _deploys_alerted
            ):
                _deploys_alerted.add(run["id"])
                commit = (run.get("head_commit") or {}).get("message", "").splitlines()
                failed.append((repo.split("/")[-1], run["head_sha"][:7], commit, run["html_url"]))

    for name, sha, commit, url in failed:
        message = f"{name} {sha}: {commit[0] if commit else ''}".strip()
        activity.record(
            "deploy", f"Deploy failed: {name}", "error", {"commit": message, "url": url}
        )
        await _push(f"Deploy failed: {name}", message, Priority.NORMAL, url)
