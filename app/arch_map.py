"""Architecture map loader: registry/map.yaml (see that file's header) plus live health.

Like the registry, the file is re-read when its mtime changes and a bad edit keeps the last good
version. `build()` folds in each node's health (from the poller's in-memory results and the
console state), container stats, and outside-service status, and adds any registry service no
node covers so the map never silently misses one.
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, field_validator, model_validator

from app.registry import load_registry

logger = logging.getLogger(__name__)

_PATH = Path(__file__).resolve().parent.parent / "registry" / "map.yaml"


class Column(BaseModel):
    x: float
    w: float


class Layout(BaseModel):
    columns: list[Column]
    row_height: float = 65
    top: float = 40
    unplaced_col: int = 3


class Signal(BaseModel):
    kind: str
    title: str | None = None
    agent: str | None = None

    @field_validator("kind", "title")
    @classmethod
    def _valid_regex(cls, v: str | None) -> str | None:
        if v is not None:
            re.compile(v)
        return v


class MapNode(BaseModel):
    id: str
    label: str
    sub: str | None = None
    kind: Literal["client", "homelab", "external"]
    col: int
    row: float
    services: list[str] = []
    services_group: str | None = None
    integrations: list[str] = []


class MapEdge(BaseModel):
    model_config = {"populate_by_name": True}

    source: str
    target: str
    signals: list[Signal] = []
    quiet: bool = False
    route: Literal["over"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_to(cls, data: Any) -> Any:
        if isinstance(data, dict) and "from" in data:
            data = {**data, "source": data.pop("from"), "target": data.pop("to", None)}
        return data


class ArchMap(BaseModel):
    layout: Layout
    nodes: list[MapNode]
    edges: list[MapEdge]

    @model_validator(mode="after")
    def _references(self):
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node ids")
        for e in self.edges:
            if e.source not in ids or e.target not in ids:
                raise ValueError(f"edge {e.source} -> {e.target} references an unknown node")
        for n in self.nodes:
            if not 0 <= n.col < len(self.layout.columns):
                raise ValueError(f"{n.id}: col {n.col} has no column in layout")
        known = {s.id for s in load_registry()}
        for n in self.nodes:
            missing = [s for s in n.services if s not in known]
            if missing:
                raise ValueError(f"{n.id}: unknown registry services {missing}")
        return self


_cache: tuple[float, ArchMap] | None = None


def load_map() -> ArchMap:
    global _cache
    mtime = _PATH.stat().st_mtime
    if _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        parsed = ArchMap(**(yaml.safe_load(_PATH.read_text(encoding="utf-8")) or {}))
    except Exception:
        if _cache:
            logger.exception("Map reload failed; keeping the last good version")
            return _cache[1]
        raise
    _cache = (mtime, parsed)
    logger.info("Architecture map loaded: %d nodes, %d edges", len(parsed.nodes), len(parsed.edges))
    return parsed


def _service_view(svc, latest: dict, states: dict | None) -> dict[str, Any]:
    live = latest.get(svc.id) or {}
    state = (states or {}).get(svc.id) or {}
    until = state.get("maintenance_until")
    return {
        "id": svc.id,
        "name": svc.name,
        "up": live.get("up"),
        "detail": live.get("detail"),
        "since": state.get("since"),
        "in_maintenance": bool(until and until > datetime.now(UTC)),
        "muted": bool(state.get("muted")),
        "container": svc.container,
    }


def _rollup(services: list[dict[str, Any]]) -> str | None:
    """down beats maintenance beats up; None when nothing is checked."""
    if not services:
        return None
    if any(s["up"] is False and not s["in_maintenance"] for s in services):
        return "down"
    if any(s["in_maintenance"] for s in services):
        return "maintenance"
    if all(s["up"] is True for s in services):
        return "up"
    return "unknown"


def build(
    latest: dict[str, dict[str, Any]],
    states: dict[str, dict[str, Any]] | None,
    containers: list[dict[str, Any]] | None,
    integrations: dict[str, Any] | None,
) -> dict[str, Any]:
    arch = load_map()
    registry = [s for s in load_registry() if s.lifecycle == "active"]
    by_id = {s.id: s for s in registry}
    by_vmid = {c["vmid"]: c for c in containers or []}
    covered = {sid for n in arch.nodes for sid in n.services}
    group_nodes = {n.services_group: n.id for n in arch.nodes if n.services_group}

    nodes: list[dict[str, Any]] = []
    for n in arch.nodes:
        members = [by_id[s] for s in n.services if s in by_id]
        if n.services_group:
            members += [
                s for s in registry if s.group == n.services_group and s.id not in covered
            ]
        views = [_service_view(s, latest, states) for s in members]
        status = _rollup(views)
        if n.integrations and integrations:
            checks = [integrations.get(k) or {} for k in n.integrations]
            status = "up" if all(c.get("status") == "ok" for c in checks) else "down"
        vmids = sorted({s.container for s in members if s.container is not None})
        nodes.append(
            {
                **n.model_dump(exclude={"services", "services_group", "integrations"}),
                "sub": n.sub or (f"{len(views)} services" if n.services_group else None),
                "status": status,
                "services": views,
                "containers": [by_vmid[v] for v in vmids if v in by_vmid],
                "placed": True,
            }
        )

    # Active registry services no node covers: shown, flagged, so the map can't go stale.
    grouped = {s.id for s in registry if s.group in group_nodes}
    unplaced = [s for s in registry if s.id not in covered and s.id not in grouped]
    col = arch.layout.unplaced_col
    next_row = max((n.row for n in arch.nodes if n.col == col), default=-1) + 1
    for i, svc in enumerate(unplaced):
        view = _service_view(svc, latest, states)
        nodes.append(
            {
                "id": f"svc:{svc.id}",
                "label": svc.name,
                "sub": "not on the map yet",
                "kind": "homelab",
                "col": col,
                "row": next_row + i,
                "status": _rollup([view]),
                "services": [view],
                "containers": [by_vmid[svc.container]] if svc.container in by_vmid else [],
                "placed": False,
            }
        )

    return {
        "layout": arch.layout.model_dump(),
        "nodes": nodes,
        "edges": [
            {"from": e.source, "to": e.target, **e.model_dump(exclude={"source", "target"})}
            for e in arch.edges
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }
