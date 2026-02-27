"""Finance router — subscriptions, budget, income, and monthly summary."""

import decimal
import logging
from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

_pool: Optional[asyncpg.Pool] = None

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    amount      NUMERIC(10,2) NOT NULL,
    frequency   TEXT NOT NULL DEFAULT 'monthly',
    category    TEXT NOT NULL,
    active      BOOLEAN DEFAULT TRUE,
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS budget (
    category      TEXT PRIMARY KEY,
    monthly_limit NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS income (
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source    TEXT NOT NULL,
    amount    NUMERIC(10,2) NOT NULL,
    frequency TEXT NOT NULL DEFAULT 'monthly',
    active    BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS transactions (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date       DATE NOT NULL,
    amount     NUMERIC(10,2) NOT NULL,
    merchant   TEXT,
    category   TEXT,
    notes      TEXT,
    source     TEXT DEFAULT 'manual',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
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
        logger.info("Finance DB pool ready")
    return _pool


def _row(rec: asyncpg.Record) -> dict:
    """Convert asyncpg Record to dict, casting Decimals to float."""
    out = {}
    for k, v in rec.items():
        out[k] = float(v) if isinstance(v, decimal.Decimal) else v
    return out


def _to_monthly(amount: float, frequency: str) -> float:
    return {
        "monthly": amount,
        "annual": amount / 12,
        "weekly": amount * 52 / 12,
        "biweekly": amount * 26 / 12,
    }.get(frequency, amount)


# ── Pydantic models ────────────────────────────────────────────────────────


class SubscriptionIn(BaseModel):
    name: str
    amount: float
    frequency: str = "monthly"
    category: str
    notes: Optional[str] = None


class SubscriptionUpdate(BaseModel):
    name: Optional[str] = None
    amount: Optional[float] = None
    frequency: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None
    notes: Optional[str] = None


class BudgetIn(BaseModel):
    monthly_limit: float


class IncomeIn(BaseModel):
    source: str
    amount: float
    frequency: str = "monthly"


class IncomeUpdate(BaseModel):
    source: Optional[str] = None
    amount: Optional[float] = None
    frequency: Optional[str] = None
    active: Optional[bool] = None


# ── Subscriptions ──────────────────────────────────────────────────────────


@router.get("/subscriptions")
async def list_subscriptions(include_inactive: bool = Query(default=False, alias="all")) -> list[dict]:
    pool = await _get_pool()
    sql = "SELECT * FROM subscriptions ORDER BY name"
    if not include_inactive:
        sql = "SELECT * FROM subscriptions WHERE active = true ORDER BY name"
    rows = await pool.fetch(sql)
    return [_row(r) for r in rows]


@router.post("/subscriptions", status_code=201)
async def create_subscription(body: SubscriptionIn) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow(
        """INSERT INTO subscriptions (name, amount, frequency, category, notes)
           VALUES ($1, $2, $3, $4, $5)
           RETURNING *""",
        body.name, body.amount, body.frequency, body.category, body.notes,
    )
    return _row(row)


@router.patch("/subscriptions/{sub_id}")
async def update_subscription(sub_id: UUID, body: SubscriptionUpdate) -> dict:
    pool = await _get_pool()
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
    row = await pool.fetchrow(
        f"UPDATE subscriptions SET {sets} WHERE id = $1 RETURNING *",
        sub_id, *fields.values(),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return _row(row)


@router.delete("/subscriptions/{sub_id}", status_code=204)
async def delete_subscription(sub_id: UUID) -> None:
    pool = await _get_pool()
    await pool.execute("UPDATE subscriptions SET active = false WHERE id = $1", sub_id)


# ── Budget ─────────────────────────────────────────────────────────────────


@router.get("/budget")
async def list_budget() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch("SELECT * FROM budget ORDER BY category")
    return [_row(r) for r in rows]


@router.put("/budget/{category}")
async def upsert_budget(category: str, body: BudgetIn) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow(
        """INSERT INTO budget (category, monthly_limit) VALUES ($1, $2)
           ON CONFLICT (category) DO UPDATE SET monthly_limit = EXCLUDED.monthly_limit
           RETURNING *""",
        category, body.monthly_limit,
    )
    return _row(row)


@router.delete("/budget/{category}", status_code=204)
async def delete_budget(category: str) -> None:
    pool = await _get_pool()
    await pool.execute("DELETE FROM budget WHERE category = $1", category)


# ── Income ─────────────────────────────────────────────────────────────────


@router.get("/income")
async def list_income() -> list[dict]:
    pool = await _get_pool()
    rows = await pool.fetch("SELECT * FROM income WHERE active = true ORDER BY source")
    return [_row(r) for r in rows]


@router.post("/income", status_code=201)
async def create_income(body: IncomeIn) -> dict:
    pool = await _get_pool()
    row = await pool.fetchrow(
        "INSERT INTO income (source, amount, frequency) VALUES ($1, $2, $3) RETURNING *",
        body.source, body.amount, body.frequency,
    )
    return _row(row)


@router.patch("/income/{income_id}")
async def update_income(income_id: UUID, body: IncomeUpdate) -> dict:
    pool = await _get_pool()
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
    row = await pool.fetchrow(
        f"UPDATE income SET {sets} WHERE id = $1 RETURNING *",
        income_id, *fields.values(),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return _row(row)


# ── Summary ────────────────────────────────────────────────────────────────


@router.get("/summary")
async def monthly_summary() -> dict:
    pool = await _get_pool()
    subs = await pool.fetch("SELECT * FROM subscriptions WHERE active = true ORDER BY name")
    income_rows = await pool.fetch("SELECT * FROM income WHERE active = true ORDER BY source")
    budget_rows = await pool.fetch("SELECT * FROM budget ORDER BY category")

    monthly_subs = sum(_to_monthly(float(r["amount"]), r["frequency"]) for r in subs)
    monthly_income = sum(_to_monthly(float(r["amount"]), r["frequency"]) for r in income_rows)

    return {
        "monthly_income": round(monthly_income, 2),
        "monthly_subscriptions": round(monthly_subs, 2),
        "net_estimated": round(monthly_income - monthly_subs, 2),
        "income_sources": [_row(r) for r in income_rows],
        "subscriptions": [_row(r) for r in subs],
        "budget": [_row(r) for r in budget_rows],
    }
