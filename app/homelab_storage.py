"""Where the homelab's disk space goes, for GET /homelab/storage and the homelab_storage tool.

Inside a container, df reports the size of its thin volume, not what the pool behind it can
still hold: CT 100's media disk says 81 GB free while the thin pool shared by every container
has about 50. So "space left for media" is the free space of whichever Proxmox storage backs
the media mount point, read from the container's config. When media moves to its own drive,
the answer follows the mount point with no code change.

Sources are independent: an unset one is null, a failing one is null and listed in `errors`,
and the rest still return.

Sizes and counts only, never titles or torrent names: Pryce doesn't want what he watches shown
on the console or handed to MCP clients. Names are one click away in Sonarr/Radarr/qBittorrent.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import settings
from app.http_client import get_client
from app.routers import homelab

logger = logging.getLogger(__name__)


def _pool_view(s: dict) -> dict:
    total, used = s.get("total") or 0, s.get("used") or 0
    return {
        "name": s.get("storage"),
        "type": s.get("type"),
        "used_bytes": used,
        "total_bytes": total,
        "free_bytes": s.get("avail", total - used),
    }


def _media_volume(config: dict) -> tuple[str | None, str | None]:
    """(volume, storage id) of the mount point at settings.media_mount, else the rootfs."""
    for key, value in config.items():
        parts = str(value).split(",")
        if key.startswith("mp") and f"mp={settings.media_mount}" in parts:
            # A bind mount ("/srv/media") isn't a Proxmox storage, so Proxmox can't size it.
            return parts[0], None if parts[0].startswith("/") else parts[0].split(":")[0]
    rootfs = str(config.get("rootfs", "")).split(",")[0]
    return (rootfs, rootfs.split(":")[0]) if rootfs else (None, None)


def _arrs() -> list[tuple[str, str]]:
    pairs = [
        (settings.sonarr_url, settings.sonarr_api_key),
        (settings.radarr_url, settings.radarr_api_key),
    ]
    return [(url, key) for url, key in pairs if url and key]


async def _arr(base: str, key: str, path: str, params: dict | None = None) -> Any:
    r = await get_client().get(
        f"{base.rstrip('/')}/api/v3{path}", params=params, headers={"X-Api-Key": key}, timeout=10.0
    )
    r.raise_for_status()
    return r.json()


async def _series() -> list[int]:
    rows = await _arr(settings.sonarr_url, settings.sonarr_api_key, "/series")
    return [s["statistics"].get("sizeOnDisk", 0) for s in rows]


async def _movies() -> list[int]:
    rows = await _arr(settings.radarr_url, settings.radarr_api_key, "/movie")
    return [m.get("sizeOnDisk", 0) for m in rows]


async def _imported(torrent_hash: str) -> bool:
    """Whether Sonarr or Radarr imported this torrent; their history keys downloads by hash."""
    for url, key in _arrs():
        history = await _arr(url, key, "/history", {"downloadId": torrent_hash, "pageSize": 50})
        if any(r.get("eventType") == "downloadFolderImported" for r in history["records"]):
            return True
    return False


async def _downloads() -> dict:
    r = await get_client().get(
        f"{settings.qbittorrent_url.rstrip('/')}/api/v2/torrents/info", timeout=10.0
    )
    r.raise_for_status()
    torrents = r.json()
    done = [t for t in torrents if t.get("progress") == 1]
    # With neither Sonarr nor Radarr there's nothing to compare against, so nothing is flagged.
    imported = (
        await asyncio.gather(*(_imported(t["hash"].upper()) for t in done))
        if _arrs()
        else [True] * len(done)
    )
    stuck = [t.get("size", 0) for t, ok in zip(done, imported, strict=True) if not ok]
    return {
        "torrents": len(torrents),
        "bytes": sum(t.get("size", 0) for t in torrents),
        "not_imported": {"count": len(stuck), "bytes": sum(stuck)},
    }


async def _source(
    name: str, enabled: Any, fetch: Callable[[], Awaitable[Any]], errors: list[str]
) -> Any:
    if not enabled:
        return None
    try:
        return await fetch()
    except Exception as e:
        logger.warning("Storage view: %s failed: %s", name, e)
        errors.append(f"{name}: {getattr(e, 'detail', None) or e}")
        return None


def _totals(sizes: list[int] | None) -> dict | None:
    if sizes is None:
        return None
    on_disk = [b for b in sizes if b]
    return {"count": len(on_disk), "bytes": sum(on_disk)}


async def overview() -> dict:
    errors: list[str] = []
    media_config_path = homelab._node(f"/lxc/{settings.media_vmid}/config")
    storages, containers, media_config, series, movies, downloads = await asyncio.gather(
        homelab._pve("GET", homelab._node("/storage"), settings.proxmox_read_token),
        homelab.list_containers(),
        _source(
            f"CT {settings.media_vmid} config",
            True,
            lambda: homelab._pve("GET", media_config_path, settings.proxmox_read_token),
            errors,
        ),
        _source("Sonarr", settings.sonarr_url and settings.sonarr_api_key, _series, errors),
        _source("Radarr", settings.radarr_url and settings.radarr_api_key, _movies, errors),
        _source("qBittorrent", settings.qbittorrent_url, _downloads, errors),
    )
    pools = [_pool_view(s) for s in storages or [] if s.get("active") and s.get("total")]
    volume, pool_name = _media_volume(media_config or {})
    media_pool = next((p for p in pools if p["name"] == pool_name), None)

    return {
        "pools": pools,
        "media": {
            "vmid": settings.media_vmid,
            "mount": settings.media_mount,
            "volume": volume,
            "pool": pool_name,
            "free_bytes": media_pool["free_bytes"] if media_pool else None,
            "series": _totals(series),
            "movies": _totals(movies),
            "downloads": downloads,
        },
        # Proxmox reports each container's root disk only; extra mount points (CT 100's media
        # disk) are what the media section covers.
        "containers": sorted(
            (
                {
                    "vmid": c["vmid"],
                    "name": c["name"],
                    "status": c["status"],
                    "used_bytes": c["disk_used_bytes"],
                    "total_bytes": c["disk_total_bytes"],
                }
                for c in containers
            ),
            key=lambda c: c["used_bytes"] or 0,
            reverse=True,
        ),
        "errors": errors,
    }
