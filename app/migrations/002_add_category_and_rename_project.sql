-- Add `category` (top-level axis: 'career' | 'personal') and rename the
-- existing `project` column to `subcategory`, which is the natural name once
-- there are multiple categories.
--
-- Existing rows are backfilled to category='career' via the column default.

ALTER TABLE journal_entries
    ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'career';

-- Guarded rename so the migration is safe to re-run by hand if needed.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'journal_entries' AND column_name = 'project'
    ) THEN
        ALTER TABLE journal_entries RENAME COLUMN project TO subcategory;
    END IF;
END $$;

DROP INDEX IF EXISTS idx_journal_project;
CREATE INDEX IF NOT EXISTS idx_journal_subcategory ON journal_entries (subcategory);
CREATE INDEX IF NOT EXISTS idx_journal_category_date
    ON journal_entries (category, entry_date DESC);
