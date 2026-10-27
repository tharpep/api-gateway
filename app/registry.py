"""Service registry loader — definitions from registry/services.yaml (see that file's header).

Runtime state lives in Postgres (app/service_health.py); this module only parses and validates
the definitions. The file is re-read when its mtime changes, so an edit is picked up without a
restart, and a bad edit keeps the last good registry instead of blanking the Homelab page.

REGISTRY_PRIVATE_PATH names a second file, kept on the homelab and out of this public repo, for
services Pryce doesn't want named publicly. Its entries are merged in and marked `private`, which
the console hides until he reveals them.
"""

import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "registry" / "services.yaml"


class HealthCheck(BaseModel):
    type: Literal["http", "tcp", "container", "proxmox_host"]
    url: str | None = None
    host: str | None = None
    port: int | None = None

    @model_validator(mode="after")
    def _fields_for_type(self):
        if self.type == "http" and not self.url:
            raise ValueError("http health check needs url")
        if self.type == "tcp" and not (self.host and self.port):
            raise ValueError("tcp health check needs host and port")
        return self


class ServiceDef(BaseModel):
    id: str
    name: str
    group: Literal["sazed", "apps", "infra"]
    container: int | None = None
    lifecycle: Literal["active", "planned", "retiring"] = "active"
    description: str = ""
    open_url: str | None = None
    icon: str | None = None
    health: HealthCheck | None = None
    depends_on: list[str] = []
    repo: str | None = None
    env_keys: list[str] = []
    private: bool = False  # set for every entry from the private file

    @model_validator(mode="after")
    def _container_check_needs_container(self):
        if self.health and self.health.type == "container" and self.container is None:
            raise ValueError(f"{self.id}: container health check needs a container")
        return self


def _entries(path: Path) -> list[dict]:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services", [])


def _parse(path: Path, private_path: Path | None) -> list[ServiceDef]:
    services = [ServiceDef(**entry) for entry in _entries(path)]
    if private_path:
        services += [ServiceDef(**{**e, "private": True}) for e in _entries(private_path)]
    ids = [s.id for s in services]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate service ids: {sorted(duplicates)}")
    unknown = {(s.id, d) for s in services for d in s.depends_on if d not in ids}
    if unknown:
        raise ValueError(f"depends_on references unknown ids: {sorted(unknown)}")
    return services


_cache: tuple[tuple[float, float], list[ServiceDef]] | None = None


def load_registry() -> list[ServiceDef]:
    """Current definitions, re-parsed only when either file changes."""
    global _cache
    path = Path(settings.registry_path) if settings.registry_path else _DEFAULT_PATH
    private = Path(settings.registry_private_path) if settings.registry_private_path else None
    if private and not private.exists():
        # Set but missing is a deploy mistake, not a reason to drop the whole registry.
        logger.warning("Private registry %s not found; loading the public one only", private)
        private = None
    mtime = (path.stat().st_mtime, private.stat().st_mtime if private else 0.0)
    if _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        services = _parse(path, private)
    except Exception:
        if _cache:
            logger.exception("Registry reload failed; keeping the last good version")
            return _cache[1]
        raise
    _cache = (mtime, services)
    logger.info("Service registry loaded: %d services from %s", len(services), path)
    return services


def get_service(service_id: str) -> ServiceDef | None:
    return next((s for s in load_registry() if s.id == service_id), None)
