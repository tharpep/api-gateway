"""Service registry loader — definitions from registry/services.yaml (see that file's header).

Runtime state lives in Postgres (app/service_health.py); this module only parses and validates
the definitions. The file is re-read when its mtime changes, so an edit is picked up without a
restart, and a bad edit keeps the last good registry instead of blanking the Homelab page.
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
    health: HealthCheck | None = None
    depends_on: list[str] = []
    repo: str | None = None
    env_keys: list[str] = []

    @model_validator(mode="after")
    def _container_check_needs_container(self):
        if self.health and self.health.type == "container" and self.container is None:
            raise ValueError(f"{self.id}: container health check needs a container")
        return self


def _parse(path: Path) -> list[ServiceDef]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    services = [ServiceDef(**entry) for entry in raw.get("services", [])]
    ids = [s.id for s in services]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate service ids: {sorted(duplicates)}")
    unknown = {(s.id, d) for s in services for d in s.depends_on if d not in ids}
    if unknown:
        raise ValueError(f"depends_on references unknown ids: {sorted(unknown)}")
    return services


_cache: tuple[float, list[ServiceDef]] | None = None


def load_registry() -> list[ServiceDef]:
    """Current definitions, re-parsed only when the file changes."""
    global _cache
    path = Path(settings.registry_path) if settings.registry_path else _DEFAULT_PATH
    mtime = path.stat().st_mtime
    if _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        services = _parse(path)
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
