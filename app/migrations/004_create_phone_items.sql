-- Things sent to Pryce's phone that don't fit in a Pushover message: long documents and files.
-- The notification links to GET /notify/items/{id}. Rows expire after 30 days and are purged
-- whenever a new item is stored.

CREATE TABLE IF NOT EXISTS phone_items (
    id           UUID PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT,
    link         TEXT,               -- the caller's own url, shown as a button on the page
    file_name    TEXT,
    content_type TEXT,
    file_data    BYTEA
);

CREATE INDEX IF NOT EXISTS phone_items_expires_idx ON phone_items (expires_at);
