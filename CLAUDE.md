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

**Entry point:** `app/main.py` — creates FastAPI app, registers middleware (CORS, rate limiting), and mounts all routers with optional API key auth via `Depends(verify_api_key)`.

**Configuration:** `app/config.py` — single `Settings` class using pydantic-settings, loads from `.env` file. All env vars have empty-string defaults so the app starts without them. Accessed via the `settings` singleton.

**Authentication:** `app/dependencies.py` — `verify_api_key()` accepts `X-API-Key` header or `Authorization: Bearer` token. Auth is **disabled** when `API_KEY` env var is empty (local dev mode). Applied as a dependency on all routers except `/health`.

### AI Provider System (`app/providers/`)

Abstract `BaseProvider` in `base.py` defines the interface: `chat()`, `chat_stream()`, `get_models()`, `supports_model()`. Request/response models are OpenAI-compatible (defined as Pydantic models in the same file).

Two implementations:
- `anthropic.py` — Direct Anthropic API client; converts OpenAI message format to Anthropic format
- `openrouter.py` — Proxies to OpenRouter; routes models by prefix (`openai/`, `deepseek/`, `google/`, etc.)

`app/routers/ai.py` lazily initializes provider singletons, selects provider based on model name, and exposes OpenAI-compatible endpoints. Rate limited to 60 req/min via slowapi.

### Google OAuth (`app/auth/google.py`)

Stateless OAuth helper with token refresh. Routers that use Google APIs cache the access token in-memory and refresh on 401 responses. Initial refresh token obtained via `get_google_token.py` utility script.

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
- `storage.py` — Google Drive KB subfolders (General, Projects, Purdue, Career, Reference):
  - `GET /storage/files` — list files; optional `?category=` filter, each file includes `category` field
  - `GET /storage/files/{file_id}/content` — download file content
  - Folder IDs are cached in-memory. Missing subfolders are skipped with a warning.
- `kb.py` — proxy to the knowledge-base service (requires `KB_SERVICE_URL`; optional `KB_SERVICE_KEY`):
  - `POST /kb/search` — hybrid KB search
  - `POST /kb/sync` — trigger Drive → KB sync (optional `force=true`)
  - `GET /kb/sources` — list tracked KB source files
  - `GET /kb/files` — list indexed files with chunk counts
  - `GET /kb/stats` — chunk and file counts
  - `DELETE /kb/files/{drive_file_id}` — remove a file from the index
  - `DELETE /kb` — clear entire KB index

**Stub / not yet implemented:**
- `context.py` — `GET /context/now` returns placeholder; aggregated context snapshot not built yet
- `webhooks.py` — `POST /webhooks/ingest` accepts payloads but source-specific handling not implemented

## Key Conventions

- **Ruff** for linting and formatting: line length 100, target Python 3.11, rules: E, F, I, UP
- All external API calls use **httpx** async client
- Streaming AI responses use **Server-Sent Events** (SSE) format
- CORS allows `localhost:3000` and `localhost:3001` by default (configurable in settings)
- Docker uses `PORT` env var (Cloud Run sets 8080), falls back to 8000
- Google API routers all follow the same pattern: module-level `_cached_token` + `_oauth` singleton, `_get_access_token()` helper that refreshes on expiry, auto-retry once on 401
