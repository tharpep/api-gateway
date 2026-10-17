# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Personal API gateway built with **FastAPI** (Python 3.11+) that provides a unified interface for multiple external services. Part of a larger personal AI ecosystem — see `developmentplan.md` for the full architecture.

Managed with **Poetry**. Deployed via **Docker** to **GCP Cloud Run** through GitHub Actions CI/CD.

## Commands

```bash
# Install dependencies
poetry install

# Run dev server (hot reload)
poetry run uvicorn app.main:app --reload

# Lint
ruff check app/

# Format
ruff format app/
```

No test suite exists yet.

## Architecture

**Entry point:** `app/main.py` — creates FastAPI app, registers middleware (CORS, rate limiting), runs DB migrations on startup via lifespan, and mounts all routers with optional API key auth via `Depends(verify_api_key)`.

**Configuration:** `app/config.py` — single `Settings` class using pydantic-settings, loads from `.env` file. All env vars have empty-string defaults so the app starts without them. Accessed via the `settings` singleton.

**Authentication:** `app/dependencies.py` — `verify_api_key()` accepts `X-API-Key` header or `Authorization: Bearer` token. Auth is **disabled** when `API_KEY` env var is empty (local dev mode). Applied as a dependency on all routers except `/health`.

### Migrations (`app/migrations/`)

Forward-only numbered SQL migrations. Files named `NNN_name.sql` are applied in order on app startup (via the FastAPI lifespan in `main.py`) inside a transaction. A `schema_migrations(version, name, applied_at)` table tracks what's been run. Failures crash startup so Cloud Run keeps the previous revision live instead of running half-applied schema. To add a new change, drop the next numbered SQL file in this directory.

### AI Provider System (`app/providers/`)

Abstract `BaseProvider` in `base.py` defines the interface: `chat()`, `chat_stream()`, `get_models()`, `supports_model()`. Request/response models are OpenAI-compatible (defined as Pydantic models in the same file).

Two implementations:
- `anthropic.py` — Direct Anthropic API client; converts OpenAI message format to Anthropic format
- `openrouter.py` — Proxies to OpenRouter; routes models by prefix (`openai/`, `deepseek/`, `google/`, etc.)

`app/routers/ai.py` lazily initializes provider singletons, selects provider based on model name, and exposes OpenAI-compatible endpoints. Rate limited to 60 req/min via slowapi.

### Google OAuth (`app/auth/google.py`, `app/auth/token_manager.py`)

`google.py` is a stateless OAuth helper (exchange/refresh/expiry-check). `token_manager.py` holds the one shared access-token cache and refresh lock — all Google-backed routers use the same refresh token (see `ALL_SCOPES`), so they use `token_manager.get_access_token()` / `token_manager.google_request(client, method, url, ...)` instead of keeping their own per-router cache. Initial refresh token obtained via `get_google_token.py` utility script.

### Routers (`app/routers/`)

Each router is a FastAPI `APIRouter`. Auth applied as a dependency on all except `health`.

**Fully implemented:**

- `health.py` — `GET /health` (public)
- `notify.py` — `POST /notify` via Pushover
- `ai.py` — OpenAI-compatible LLM proxy (`/ai/v1/chat/completions`, `/ai/v1/chat/completions/stream`, `/ai/v1/models`)
- `calendar.py` — Google Calendar full CRUD:
  - `GET /calendar/today`, `GET /calendar/events`, `GET /calendar/availability`
  - `POST /calendar/events`, `PATCH /calendar/events/{id}`, `DELETE /calendar/events/{id}`
- `tasks.py` — Google Tasks full CRUD:
  - `GET /tasks/upcoming`, `GET /tasks/lists`, `GET /tasks/lists/{list_id}/tasks`
  - `POST /tasks/lists/{list_id}/tasks`, `PATCH /tasks/lists/{list_id}/tasks/{task_id}`, `DELETE /tasks/lists/{list_id}/tasks/{task_id}`
- `email.py` — Gmail read + draft:
  - `GET /email/recent`, `GET /email/unread`, `GET /email/search`, `GET /email/messages/{id}`
  - `POST /email/draft`
  - **Missing:** `POST /email/send`, `POST /email/reply/{id}`
- `storage.py` — Google Drive KB subfolders, discovered dynamically from folders directly under "Knowledge Base" (no hardcoded list — dropping a new Drive folder there makes it a category with no code change):
  - `GET /storage/files` — list files; optional `?category=` filter (case-insensitive match against subfolder names), each file includes `category` field
  - `GET /storage/files/{file_id}/content` — download file content
  - Folder IDs are cached indefinitely; the subfolder list is cached for 5 minutes.
- `kb.py` — proxy to the knowledge-base service (requires `KB_SERVICE_URL`; optional `KB_SERVICE_KEY`):
  - `POST /kb/search` — hybrid KB search
  - `POST /kb/sync` — trigger Drive → KB sync (optional `force=true`)
  - `GET /kb/sources` — list tracked KB source files
  - `GET /kb/files` — list indexed files with chunk counts
  - `GET /kb/stats` — chunk and file counts
  - `DELETE /kb/files/{drive_file_id}` — remove a file from the index
  - `DELETE /kb` — clear entire KB index
- `journal.py` — daily contributions log backed by Postgres `journal_entries`. Two-level taxonomy: `category` (`career` | `personal`, defaults to `career`) and `subcategory` (free-form, e.g. `eli-lilly`). Schema is owned by `app/migrations/`.
  - `GET /journal/entries` — list with filters `category`, `subcategory`, `tag`, `q` (ILIKE on title+body), `start_date`, `end_date`, `limit`, `cursor`. Returns `{entries, next_cursor}`. `project=` accepted as deprecated alias for `subcategory`.
  - `GET /journal/entries/{id}`, `POST /journal/entries`, `PATCH /journal/entries/{id}`, `DELETE /journal/entries/{id}`
  - `GET /journal/categories` — fixed list `['career', 'personal']`
  - `GET /journal/subcategories?category=` — distinct subcategory names
  - `GET /journal/projects` — deprecated alias for `/subcategories`, kept for one release
  - `GET /journal/summary` — entries grouped by date with stats; takes `category`, `subcategory`, `period` (`week` | `month` | `last_week` | `last_month`), or explicit date range
  - `GET /journal/export` — markdown or plain text export with the same filters as `/summary`

- `homelab.py` — Proxmox host and container status plus power actions (requires `PROXMOX_HOST`, `PROXMOX_READ_TOKEN`; actions need `PROXMOX_POWER_TOKEN`; `PROXMOX_CA_CERT` points at `pve-root-ca.pem`). Uses its own httpx client because Proxmox has its own CA. Which containers can be started/restarted is decided by the power token's Proxmox ACLs, not by this service; a Proxmox 403 is returned as 403.
  - `GET /homelab/host` — CPU, memory, root disk, uptime, storage pools
  - `GET /homelab/containers` — all LXC containers with status and usage
  - `GET /homelab/containers/{vmid}` — status, key config, recent start/stop/reboot history
  - `POST /homelab/containers/{vmid}/restart`, `POST /homelab/containers/{vmid}/start`

### MCP (`app/mcp_server.py`)

Streamable-HTTP MCP server (official `mcp` SDK, FastMCP, stateless + JSON responses) mounted at `/mcp/` (trailing slash). Tools wrap the router functions directly so MCP and REST share one code path and the same limits. The API key is checked by an ASGI guard around the mounted app, since FastAPI dependencies don't reach mounts. The session manager runs inside the FastAPI lifespan. Clients connect at `https://sazed.tail77c590.ts.net/gw/mcp/` (Caddy on CT 106 injects the key). Current tools: `homelab_overview`, `homelab_container`, `homelab_restart_container`, `homelab_start_container`.

**Stub / not yet implemented:**
- `context.py` — `GET /context/now` returns placeholder; aggregated context snapshot not built yet
- `webhooks.py` — `POST /webhooks/ingest` accepts payloads but source-specific handling not implemented

## Key Conventions

- **Ruff** for linting and formatting: line length 100, target Python 3.11, rules: E, F, I, UP
- All external API calls use **httpx**, via one pooled `httpx.AsyncClient` (`app/http_client.py`, opened/closed by the FastAPI lifespan) — call `get_client()` rather than constructing a new client per request
- Streaming AI responses use **Server-Sent Events** (SSE) format
- CORS allows `localhost:3000` and `localhost:3001` by default (configurable in settings)
- Docker uses `PORT` env var (Cloud Run sets 8080), falls back to 8000
- Google API routers all call `app.auth.token_manager.google_request()` for authenticated requests — a single shared access-token cache (behind an `asyncio.Lock`) and 401-retry, instead of a per-router copy
- Schema changes go in `app/migrations/` as numbered SQL files; never use lazy `CREATE TABLE` inside a router
