"""Journal router — daily contributions log with category/subcategory/tag support.

Two-level taxonomy:
- `category`: top-level axis ('career' or 'personal').
- `subcategory`: free-form sub-axis under a category (e.g. 'eli-lilly').

Cursor pagination is keyed on (entry_date DESC, created_at DESC, id DESC) so a
cursor uniquely identifies a position even when many entries share a date.
"""

import base64
import decimal
import json
import logging
from datetime import date, datetime, timedelta
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: asyncpg.Pool | None = None

CATEGORIES: tuple[str, ...] = ("career", "personal")


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise HTTPException(status_code=503, detail="Database not configured (DATABASE_URL)")
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=5)
        logger.info("Journal DB pool ready")
    return _pool


def _row(rec: asyncpg.Record) -> dict:
    out = {}
    for k, v in rec.items():
        if isinstance(v, decimal.Decimal):
            out[k] = float(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, date):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _encode_cursor(entry_date: str, created_at: str, entry_id: str) -> str:
    raw = json.dumps([entry_date, created_at, entry_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[date, datetime, UUID]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        ed_s, ca_s, id_s = json.loads(raw)
        return date.fromisoformat(ed_s), datetime.fromisoformat(ca_s), UUID(id_s)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid cursor: {e}")


# ── Pydantic models ──────────────────────────────────────────────────────


class EntryIn(BaseModel):
    category: str = "career"
    subcategory: str
    title: str
    body: str
    entry_date: date | None = None
    tags: list[str] = []


class EntryUpdate(BaseModel):
    category: str | None = None
    subcategory: str | None = None
    title: str | None = None
    body: str | None = None
    entry_date: date | None = None
    tags: list[str] | None = None


# ── Entries ───────────────────────────────────────────────────────────────


@router.get("/entries")
async def list_entries(
    category: str | None = Query(default=None),
    subcategory: str | None = Query(default=None),
    project: str | None = Query(
        default=None, deprecated=True, description="Alias for subcategory."
    ),
    tag: str | None = Query(default=None),
    q: str | None = Query(
        default=None, description="Case-insensitive text search on title + body."
    ),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    cursor: str | None = Query(
        default=None, description="Opaque cursor from a previous response."
    ),
) -> dict:
    pool = await _get_pool()

    sub = subcategory or project
    conditions: list[str] = []
    params: list = []
    idx = 1

    if category:
        conditions.append(f"category = ${idx}")
        params.append(category)
        idx += 1
    if sub:
        conditions.append(f"subcategory = ${idx}")
        params.append(sub)
        idx += 1
    if tag:
        conditions.append(f"${idx} = ANY(tags)")
        params.append(tag)
        idx += 1
    if q:
        conditions.append(f"(title ILIKE ${idx} OR body ILIKE ${idx})")
        params.append(f"%{q}%")
        idx += 1
    if start_date:
        conditions.append(f"entry_date >= ${idx}")
        params.append(start_date)
        idx += 1
    if end_date:
        conditions.append(f"entry_date <= ${idx}")
        params.append(end_date)
        idx += 1

    if cursor:
        c_date, c_created, c_id = _decode_cursor(cursor)
        conditions.append(
            f"(entry_date, created_at, id) < (${idx}, ${idx + 1}, ${idx + 2})"
        )
        params.extend([c_date, c_created, c_id])
        idx += 3

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    # Ask for limit+1 to know whether another page exists.
    params.append(limit + 1)

    sql = (
        f"SELECT * FROM journal_entries {where} "
        f"ORDER BY entry_date DESC, created_at DESC, id DESC LIMIT ${idx}"
    )
    rows = await pool.fetch(sql, *params)

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor: str | None = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(
            last["entry_date"].isoformat(),
            last["created_at"].isoformat(),
            str(last["id"]),
        )

    return {"entries": [_row(r) for r in page], "next_cursor": next_cursor}


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
        """INSERT INTO journal_entries
               (entry_date, category, subcategory, title, body, tags)
           VALUES (COALESCE($1, CURRENT_DATE), $2, $3, $4, $5, $6)
           RETURNING *""",
        body.entry_date,
        body.category,
        body.subcategory,
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


# ── Taxonomy ─────────────────────────────────────────────────────────────


@router.get("/categories")
async def list_categories() -> list[str]:
    """Return the fixed top-level categories."""
    return list(CATEGORIES)


@router.get("/subcategories")
async def list_subcategories(category: str | None = Query(default=None)) -> list[str]:
    """Return distinct subcategory names, optionally filtered by category."""
    pool = await _get_pool()
    if category:
        rows = await pool.fetch(
            "SELECT DISTINCT subcategory FROM journal_entries WHERE category = $1 "
            "ORDER BY subcategory",
            category,
        )
    else:
        rows = await pool.fetch(
            "SELECT DISTINCT subcategory FROM journal_entries ORDER BY subcategory"
        )
    return [r["subcategory"] for r in rows]


@router.get("/projects", deprecated=True)
async def list_projects() -> list[str]:
    """Deprecated alias for /subcategories; kept for one release."""
    return await list_subcategories(category=None)


# ── Summary ──────────────────────────────────────────────────────────────


def _default_range(period: str) -> tuple[date, date]:
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
    category: str | None = Query(default=None),
    subcategory: str | None = Query(default=None),
    project: str | None = Query(default=None, deprecated=True),
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

    sub = subcategory or project
    pool = await _get_pool()
    conditions = ["entry_date >= $1", "entry_date <= $2"]
    params: list = [start_date, end_date]
    idx = 3

    if category:
        conditions.append(f"category = ${idx}")
        params.append(category)
        idx += 1
    if sub:
        conditions.append(f"subcategory = ${idx}")
        params.append(sub)
        idx += 1

    where = "WHERE " + " AND ".join(conditions)
    sql = (
        f"SELECT * FROM journal_entries {where} "
        f"ORDER BY entry_date DESC, created_at DESC"
    )
    rows = await pool.fetch(sql, *params)
    entries = [_row(r) for r in rows]

    by_date: dict[str, list[dict]] = {}
    for e in entries:
        by_date.setdefault(e["entry_date"], []).append(e)

    all_tags: dict[str, int] = {}
    all_categories: set[str] = set()
    all_subs: set[str] = set()
    for e in entries:
        all_categories.add(e["category"])
        all_subs.add(e["subcategory"])
        for t in e.get("tags", []):
            all_tags[t] = all_tags.get(t, 0) + 1

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_entries": len(entries),
        "categories": sorted(all_categories),
        "subcategories": sorted(all_subs),
        "top_tags": sorted(all_tags.items(), key=lambda x: -x[1])[:10],
        "days": [{"date": d, "entries": es} for d, es in by_date.items()],
    }


# ── Export ────────────────────────────────────────────────────────────────


@router.get("/export", response_class=PlainTextResponse)
async def export_entries(
    category: str | None = Query(default=None),
    subcategory: str | None = Query(default=None),
    project: str | None = Query(default=None, deprecated=True),
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

    sub = subcategory or project
    pool = await _get_pool()
    conditions = ["entry_date >= $1", "entry_date <= $2"]
    params: list = [start_date, end_date]
    idx = 3

    if category:
        conditions.append(f"category = ${idx}")
        params.append(category)
        idx += 1
    if sub:
        conditions.append(f"subcategory = ${idx}")
        params.append(sub)
        idx += 1

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
    scope_bits = [b for b in (category, sub) if b]
    if scope_bits:
        title += f" ({' / '.join(scope_bits)})"
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

        scope = f"{e['category']} / {e['subcategory']}"
        if md:
            lines.append(f"### {e['title']}")
            lines.append(f"*{scope}*{tags}")
        else:
            lines.append(f"  {e['title']} ({scope}){tags}")

        lines.append("")
        lines.append(e["body"])
        lines.append("")

    return "\n".join(lines)
