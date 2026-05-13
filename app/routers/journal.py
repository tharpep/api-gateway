"""Journal router — daily work contributions log with project/tag support."""

import decimal
import logging
from datetime import date, timedelta
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: asyncpg.Pool | None = None

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS journal_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    project     TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    tags        TEXT[] DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_journal_date ON journal_entries (entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_journal_project ON journal_entries (project);
"""


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL)")
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
        async with _pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
        logger.info("Journal DB pool ready")
    return _pool


def _row(rec: asyncpg.Record) -> dict:
    out = {}
    for k, v in rec.items():
        if isinstance(v, decimal.Decimal):
            out[k] = float(v)
        elif isinstance(v, date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ── Pydantic models ──────────────────────────────────────────────────────


class EntryIn(BaseModel):
    entry_date: date | None = None
    project: str
    title: str
    body: str
    tags: list[str] = []


class EntryUpdate(BaseModel):
    entry_date: date | None = None
    project: str | None = None
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None


# ── Entries ───────────────────────────────────────────────────────────────


@router.get("/entries")
async def list_entries(
    project: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
) -> list[dict]:
    pool = await _get_pool()
    conditions: list[str] = []
    params: list = []
    idx = 1

    if project:
        conditions.append(f"project = ${idx}")
        params.append(project)
        idx += 1
    if tag:
        conditions.append(f"${idx} = ANY(tags)")
        params.append(tag)
        idx += 1
    if start_date:
        conditions.append(f"entry_date >= ${idx}")
        params.append(start_date)
        idx += 1
    if end_date:
        conditions.append(f"entry_date <= ${idx}")
        params.append(end_date)
        idx += 1

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)

    sql = (
        f"SELECT * FROM journal_entries {where} "
        f"ORDER BY entry_date DESC, created_at DESC LIMIT ${idx}"
    )
    rows = await pool.fetch(sql, *params)
    return [_row(r) for r in rows]


@router.get("/entries/{entry_id}")
async def get_entry(entry_id: UUID) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow("SELECT * FROM journal_entries WHERE id = $1", entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    return _row(row)


@router.post("/entries", status_code=201)
async def create_entry(body: EntryIn) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow(
        """INSERT INTO journal_entries (entry_date, project, title, body, tags)
           VALUES (COALESCE($1, CURRENT_DATE), $2, $3, $4, $5)
           RETURNING *""",
        body.entry_date,
        body.project,
        body.title,
        body.body,
        body.tags,
    )
    return _row(row)


@router.patch("/entries/{entry_id}")
async def update_entry(entry_id: UUID, body: EntryUpdate) -> dict:
    pool = await _get_pool()
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    sets = "updated_at = NOW(), " + ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
    row = await pool.fetchrow(
        f"UPDATE journal_entries SET {sets} WHERE id = $1 RETURNING *",
        entry_id,
        *fields.values(),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    return _row(row)


@router.delete("/entries/{entry_id}", status_code=204)
async def delete_entry(entry_id: UUID) -> None:
    pool = await _get_pool()
    result = await pool.execute("DELETE FROM journal_entries WHERE id = $1", entry_id)
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Entry not found")


# ── Projects ─────────────────────────────────────────────────────────────


@router.get("/projects")
async def list_projects() -> list[str]:
    pool = await _get_pool()
    rows = await pool.fetch(
        "SELECT DISTINCT project FROM journal_entries ORDER BY project"
    )
    return [r["project"] for r in rows]


# ── Summary ──────────────────────────────────────────────────────────────


def _default_range(
    period: str,
) -> tuple[date, date]:
    """Return (start, end) for common period shorthands."""
    today = date.today()
    if period == "week":
        start = today - timedelta(days=today.weekday())  # Monday
        return start, today
    if period == "month":
        return today.replace(day=1), today
    if period == "last_week":
        monday = today - timedelta(days=today.weekday())
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if period == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    return today - timedelta(days=6), today  # fallback: last 7 days


@router.get("/summary")
async def journal_summary(
    project: str | None = Query(default=None),
    period: str | None = Query(
        default=None,
        description="Shorthand: week, month, last_week, last_month.",
    ),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict:
    """Return journal entries grouped by date with stats."""
    if period and not start_date:
        start_date, end_date = _default_range(period)
    elif not start_date:
        start_date, end_date = _default_range("week")
    if not end_date:
        end_date = date.today()

    pool = await _get_pool()
    conditions = ["entry_date >= $1", "entry_date <= $2"]
    params: list = [start_date, end_date]
    idx = 3

    if project:
        conditions.append(f"project = ${idx}")
        params.append(project)

    where = "WHERE " + " AND ".join(conditions)
    sql = (
        f"SELECT * FROM journal_entries {where} "
        f"ORDER BY entry_date DESC, created_at DESC"
    )
    rows = await pool.fetch(sql, *params)
    entries = [_row(r) for r in rows]

    # Group by date
    by_date: dict[str, list[dict]] = {}
    for e in entries:
        by_date.setdefault(e["entry_date"], []).append(e)

    # Collect all tags
    all_tags: dict[str, int] = {}
    all_projects: set[str] = set()
    for e in entries:
        all_projects.add(e["project"])
        for t in e.get("tags", []):
            all_tags[t] = all_tags.get(t, 0) + 1

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_entries": len(entries),
        "projects": sorted(all_projects),
        "top_tags": sorted(all_tags.items(), key=lambda x: -x[1])[:10],
        "days": [
            {"date": d, "entries": es}
            for d, es in by_date.items()
        ],
    }


# ── Export ────────────────────────────────────────────────────────────────


@router.get("/export", response_class=PlainTextResponse)
async def export_entries(
    project: str | None = Query(default=None),
    period: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    format: str = Query(default="markdown", description="markdown or plain"),
) -> str:
    """Export journal entries as markdown or plain text."""
    if period and not start_date:
        start_date, end_date = _default_range(period)
    elif not start_date:
        start_date, end_date = _default_range("week")
    if not end_date:
        end_date = date.today()

    pool = await _get_pool()
    conditions = ["entry_date >= $1", "entry_date <= $2"]
    params: list = [start_date, end_date]
    idx = 3

    if project:
        conditions.append(f"project = ${idx}")
        params.append(project)

    where = "WHERE " + " AND ".join(conditions)
    sql = (
        f"SELECT * FROM journal_entries {where} "
        f"ORDER BY entry_date ASC, created_at ASC"
    )
    rows = await pool.fetch(sql, *params)
    entries = [_row(r) for r in rows]

    if not entries:
        return "No journal entries found for the given range."

    md = format == "markdown"
    lines: list[str] = []
    title = f"Journal: {start_date.isoformat()} to {end_date.isoformat()}"
    if project:
        title += f" ({project})"
    lines.append(f"# {title}" if md else title)
    lines.append("")

    current_date = ""
    for e in entries:
        if e["entry_date"] != current_date:
            current_date = e["entry_date"]
            d = date.fromisoformat(current_date)
            label = d.strftime("%A, %B %d, %Y")
            lines.append(f"## {label}" if md else f"--- {label} ---")
            lines.append("")

        tags = ""
        if e.get("tags"):
            if md:
                tags = " " + " ".join(f"`{t}`" for t in e["tags"])
            else:
                tags = f" [{', '.join(e['tags'])}]"

        if md:
            lines.append(f"### {e['title']}")
            lines.append(f"*{e['project']}*{tags}")
        else:
            lines.append(f"  {e['title']} ({e['project']}){tags}")

        lines.append("")
        lines.append(e["body"])
        lines.append("")

    return "\n".join(lines)
