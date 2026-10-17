-- Runtime state for registry/services.yaml entries, keyed by the registry `id`.
-- Definitions stay in git; rows for ids no longer in the registry are simply ignored.

CREATE TABLE IF NOT EXISTS service_state (
    service_id        TEXT PRIMARY KEY,
    up                BOOLEAN,              -- confirmed status (NULL = unknown)
    since             TIMESTAMPTZ,          -- when `up` last changed
    last_checked      TIMESTAMPTZ,
    last_latency_ms   INTEGER,
    last_detail       TEXT,
    maintenance_until TIMESTAMPTZ,          -- set from the console before planned work
    muted             BOOLEAN NOT NULL DEFAULT FALSE,
    notes             TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per confirmed up/down transition: outage history without a row per check.
CREATE TABLE IF NOT EXISTS service_events (
    id              BIGSERIAL PRIMARY KEY,
    service_id      TEXT NOT NULL,
    at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    up              BOOLEAN NOT NULL,
    detail          TEXT,
    in_maintenance  BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_service_events_service_at
    ON service_events (service_id, at DESC);
