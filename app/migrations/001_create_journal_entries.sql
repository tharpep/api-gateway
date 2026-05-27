-- Baseline: matches the table previously created by the journal router on first
-- request. Uses IF NOT EXISTS so it's a no-op for databases that already had
-- the legacy lazy-create path run against them.

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
