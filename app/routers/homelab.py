"""Homelab router — Proxmox host and container status, plus start/restart.

Ported from home-lab-dashboard (CT 102) so the Sazed console, Sazed's tools and MCP clients
share one scoped path to Proxmox instead of each holding their own credentials.

Two tokens, by design: reads use a PVEAuditor token; power actions use a separate token whose
Proxmox ACLs list exactly which containers may be started or restarted. A 403 from Proxmox on
an action therefore means "not allowed for this container", and is passed through as such.
"""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_LIFECYCLE_TASK_TYPES = {"vzstart", "vzstop", "vzshutdown", "vzreboot", "vzsuspend", "vzresume"}

# Proxmox uses its own CA, so it can't share the pooled client in app.http_client (httpx
# fixes `verify` per client, not per request). Created on first use, lives for the process.
_proxmox_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _proxmox_client
    if _proxmox_client is None:
        verify: str | bool = settings.proxmox_ca_cert or True
        _proxmox_client = httpx.AsyncClient(
            base_url=f"https://{settings.proxmox_host}:8006/api2/json",
            verify=verify,
            timeout=15.0,
        )
    return _proxmox_client


async def _pve(method: str, path: str, token: str) -> Any:
    if not settings.proxmox_host or not token:
        raise HTTPException(503, "Proxmox not configured (PROXMOX_HOST / token not set)")
    try:
        r = await _client().request(method, path, headers={"Authorization": f"PVEAPIToken={token}"})
    except httpx.ConnectError:
        raise HTTPException(503, "Proxmox host unreachable")
    except httpx.TimeoutException:
        raise HTTPException(504, "Proxmox API timed out")

    if r.status_code == 403:
        raise HTTPException(403, f"Proxmox denied {method} {path}")
    if r.status_code >= 400:
        logger.warning("Proxmox %s %s failed: %s %s", method, path, r.status_code, r.text[:200])
        # Proxmox reports action refusals (e.g. "CT 105 already running") as 500 with a
        # message; surface it so callers like Sazed can explain what happened.
        try:
            message = (r.json().get("message") or "").strip()
        except ValueError:
            message = ""
        raise HTTPException(502, f"Proxmox API error {r.status_code}: {message or r.reason_phrase}")
    return r.json().get("data")


def _node(path: str = "") -> str:
    return f"/nodes/{settings.proxmox_node}{path}"


def _container_summary(c: dict) -> dict:
    return {
        "vmid": int(c["vmid"]),
        "name": c.get("name"),
        "status": c.get("status", "unknown"),
        "cpu_percent": round((c.get("cpu") or 0) * 100, 1),
        "cpus": c.get("cpus"),
        "mem_used_bytes": c.get("mem"),
        "mem_total_bytes": c.get("maxmem"),
        "disk_used_bytes": c.get("disk"),
        "disk_total_bytes": c.get("maxdisk"),
        "uptime_seconds": c.get("uptime"),
        "tags": [t for t in (c.get("tags") or "").split(";") if t],
    }


@router.get("/host")
async def host_status():
    """Host CPU, memory, root disk, uptime, and storage pool usage."""
    status = await _pve("GET", _node("/status"), settings.proxmox_read_token)
    storages = await _pve("GET", _node("/storage"), settings.proxmox_read_token)
    return {
        "node": settings.proxmox_node,
        "cpu_percent": round((status.get("cpu") or 0) * 100, 1),
        "mem_used_bytes": status.get("memory", {}).get("used"),
        "mem_total_bytes": status.get("memory", {}).get("total"),
        "rootfs_used_bytes": status.get("rootfs", {}).get("used"),
        "rootfs_total_bytes": status.get("rootfs", {}).get("total"),
        "uptime_seconds": status.get("uptime"),
        "pve_version": status.get("pveversion"),
        "storage": [
            {
                "name": s.get("storage"),
                "type": s.get("type"),
                "active": bool(s.get("active")),
                "used_bytes": s.get("used"),
                "total_bytes": s.get("total"),
            }
            for s in storages or []
        ],
    }


@router.get("/storage")
async def storage():
    """Real free space per pool, space left for media, library and download sizes, and each
    container's disk use. See app/homelab_storage.py for why df inside a container misleads."""
    from app import homelab_storage  # it imports this module

    return await homelab_storage.overview()


@router.get("/containers")
async def list_containers():
    """All LXC containers with status and resource usage, sorted by VMID."""
    containers = await _pve("GET", _node("/lxc"), settings.proxmox_read_token)
    return sorted((_container_summary(c) for c in containers or []), key=lambda c: c["vmid"])


async def _lifecycle_tasks(vmid: int, limit: int) -> list[dict]:
    tasks = await _pve(
        "GET", _node(f"/tasks?vmid={vmid}&limit={limit}"), settings.proxmox_read_token
    )
    # Proxmox's own task log is what distinguishes a deliberate stop (and by whom) from a crash.
    return [
        {
            "type": t.get("type"),
            "user": t.get("user"),
            "status": t.get("status"),
            "start_time": t.get("starttime"),
            "end_time": t.get("endtime"),
        }
        for t in tasks or []
        if t.get("type") in _LIFECYCLE_TASK_TYPES
    ]


@router.get("/containers/{vmid}")
async def get_container(vmid: int, task_limit: int = Query(10, ge=1, le=50)):
    """One container: live status, key config, and recent start/stop/reboot history."""
    status = await _pve("GET", _node(f"/lxc/{vmid}/status/current"), settings.proxmox_read_token)
    config = await _pve("GET", _node(f"/lxc/{vmid}/config"), settings.proxmox_read_token)
    return {
        **_container_summary({**status, "vmid": vmid}),
        "config": {
            "hostname": config.get("hostname"),
            "cores": config.get("cores"),
            "memory_mb": config.get("memory"),
            "swap_mb": config.get("swap"),
            "onboot": bool(config.get("onboot")),
            "unprivileged": bool(config.get("unprivileged")),
        },
        "recent_lifecycle": await _lifecycle_tasks(vmid, task_limit),
    }


@router.post("/containers/{vmid}/restart")
async def restart_container(vmid: int):
    """Reboot a running container. Allowed only where the power token has VM.PowerMgmt."""
    upid = await _pve("POST", _node(f"/lxc/{vmid}/status/reboot"), settings.proxmox_power_token)
    logger.info("Restart requested for container %s (task %s)", vmid, upid)
    return {"ok": True, "vmid": vmid, "task": upid}


@router.post("/containers/{vmid}/start")
async def start_container(vmid: int):
    """Start a stopped container. Allowed only where the power token has VM.PowerMgmt."""
    upid = await _pve("POST", _node(f"/lxc/{vmid}/status/start"), settings.proxmox_power_token)
    logger.info("Start requested for container %s (task %s)", vmid, upid)
    return {"ok": True, "vmid": vmid, "task": upid}
