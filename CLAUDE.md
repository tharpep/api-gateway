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
- `notify.py` — Pushover. `POST /notify` (title, message, url; optional `document` and base64 image attachment) and `POST /notify/file` (multipart). Images up to 5 MB ride along as the attachment; a long document or any other file is stored in `phone_items` (migration 004, kept 30 days) and the notification links to `GET /notify/items/{id}` (page) and `/items/{id}/file`. Non-image files are always served as downloads, so uploaded HTML/SVG can't run script on this origin. On GCP the shared migration counter means 004 is skipped (Sazed already has a 004), so stored items only work on the homelab.
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
  - `POST /kb/ingest/file` (multipart upload), `POST /kb/ingest/text`, `POST /kb/ingest/url` — add content (Drive sync was removed 2026-10-18)
  - `GET /kb/sources` — list sources, newest first; `GET /kb/sources/{file_id}/content` — full text; `GET /kb/sources/{file_id}/export?format=md|txt` — download as a file (the proxy passes Content-Disposition through)
  - `GET /kb/index`, `GET /kb/files`, `GET /kb/stats`
  - `DELETE /kb/files/{file_id}` — remove a source and its stored text
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
  - `GET /homelab/storage` — where disk space goes (`app/homelab_storage.py`): free space per Proxmox pool; "free for media" = the pool behind CT `MEDIA_VMID`'s `MEDIA_MOUNT` (df inside a container shows its thin volume, not the shared pool, so it overstates); series/movie counts and sizes from Sonarr/Radarr; torrent count and size from qBittorrent, plus the count and size of finished ones Sonarr/Radarr never imported (history by `downloadId`); each container's root disk. Never titles or torrent names (Pryce's choice: what he watches stays off the console and out of MCP). Each source (`SONARR_URL`/`_API_KEY`, `RADARR_URL`/`_API_KEY`, `QBITTORRENT_URL`) is optional and fails independently into `errors`. qBittorrent has no key: its WebUI auth-bypass whitelist includes CT 104's IP (192.168.50.63, DHCP).
  - `GET /homelab/containers` — all LXC containers with status and usage
  - `GET /homelab/containers/{vmid}` — status, key config, recent start/stop/reboot history
  - `POST /homelab/containers/{vmid}/restart`, `POST /homelab/containers/{vmid}/start`

### Service registry + health poller (`registry/services.yaml`, `app/registry.py`, `app/service_health.py`, `app/routers/services.py`)

Hybrid by design: service **definitions** live in `registry/services.yaml` (git; field docs in the file header), and **runtime state** lives in Postgres (`service_state`, `service_events`, migration 003). `app/registry.py` validates the YAML and re-reads it when its mtime changes, keeping the last good version if an edit is invalid. `app/service_health.py` polls every `HEALTH_POLL_SECONDS` (default 60), only where `PROXMOX_HOST` is set. Check types are http, tcp, container and proxmox_host, with one Proxmox container snapshot per cycle. Latest results are held in memory, so the endpoints keep working when Postgres is down. Down needs 2 consecutive failures; up needs 1 success. Transitions are written to `service_events`.
  - `GET /homelab/services?group=` — definitions + `live` (in-memory) + `state` (DB; null if unavailable) + up/down summary
  - `GET /homelab/services/{id}` — plus recent up/down events
  - `PATCH /homelab/services/{id}/state` — `{maintenance_minutes (0 ends), muted, notes}`; omitted fields unchanged

To add a service: add an entry to `registry/services.yaml` and deploy. Never rename an `id`, because state rows hang off it.

**Private services:** this repo is public, so services Pryce doesn't want named live in a second file on CT 104, `/home/ghrunner/sazed-eco/services.private.yaml` (`REGISTRY_PRIVATE_PATH`), outside git. Same fields; its entries are merged in with `private: true` (re-read on change like the main file; a missing file logs a warning and loads the public registry alone). The console hides private services on Home and the Homelab page until Pryce reveals them, and the map's "N services" label doesn't count them.

### Architecture map (`registry/map.yaml`, `app/arch_map.py`)

The console's Homelab Map view is drawn from `registry/map.yaml`. It's pure data: grid layout (columns, rows), nodes (which registry services and integrations roll up into each dot), and edges with Activity `signals` that light them up. Change the map by editing the file; it's re-read on change, and a bad edit keeps the last good version.

`GET /homelab/map` returns it with live health per node (the poller's results plus console state: down beats maintenance beats up), container stats, and integration status for external nodes. Integration status only means "configured", not "reachable". Any active registry service no node covers is added as a dashed "not on the map yet" box in `unplaced_col`, so the map can't silently miss a service.

`GET /homelab/map/public` is the public-safe version Sazed publishes to the portfolio.
- It keeps only placed nodes with `public` true (default) and lines between them.
- Per node: label, subtitle (`public_sub`, or the subtitle minus `CT nnn`), `about`, kind, position, status, and service counts. Also overall 7-day uptime from `service_events` (`service_health.uptime_percent`).
- Never included: service ids, containers, hosts, IPs or URLs. The media apps node is `public: false`.

### Alerts (`app/alerts.py`)

Deterministic Pushover alerts run at the end of each poll cycle (so homelab only), through `notify.send_pushover`. Outages: confirmed down/up transitions, one message per cycle; muted or in-maintenance services are skipped; down is HIGH priority unless every affected service is in the `apps` group; recovery is LOW. Disk: Proxmox storage pools at `ALERT_POOL_PERCENT` (80) and running containers' root disks at `ALERT_DISK_PERCENT` (85), re-armed 5 points below; messages include GB free. Deploys: failed "Deploy to Homelab" runs for active registry repos, checked every `ALERT_DEPLOY_POLL_SECONDS` (300) with the gateway's GitHub token. The poller seeds last-known status from `service_state` at startup, so restarts don't alert. Disk and deploy dedupe are in memory (deliberately no migration: on GCP the gateway and Sazed share one `schema_migrations` version counter). `ALERTS_ENABLED=false` silences all of it. Whole-homelab-down needs an outside dead-man's switch; not set up.

### GitHub App bot (`app/routers/github_app.py`, plans/07)

The bot is `sazed-worker-tharpep[bot]`, set by `GITHUB_APP_ID` and `GITHUB_APP_KEY_B64` (the .pem, base64-encoded). Permissions: contents, issues and pull requests write; metadata read. It's installed on all of tharpep's repos except `GITHUB_APP_EXCLUDED_REPOS` (Trusted-Model-Registry, gotenberg-fork), which get a 403.
- **Tokens:** each call mints an installation token scoped to one repo and only the permissions it needs. Tokens are cached until 5 minutes before they expire. The JWT uses `exp` = now + 300, because CT 104's clock runs about a minute fast and GitHub rejects anything over 10 minutes.
- **Routes, all under `/github/app`:**
  - `POST /repos/{owner}/{repo}/issues` and `/issues/{n}/comments` (PR comments too) act as the bot.
  - `POST /clone-token` returns a read-only token for the worker to clone a private repo.
  - `POST /pull-request` takes `{repo, branch: sazed/..., title, body, commit_message, files: {path: base64 | null}}`. It builds blobs, tree, commit, ref and PR through the Git Data API (no git push), keeps existing file modes, and refuses `.git/` and `.github/workflows/` paths.
- **Why a bot:** it has no admin bypass and can't approve its own PRs, so merges stay with Pryce.
- **Your token stays for your own operations:** Sazed's `create_issue` and comment tools use these bot routes; the PAT routes under `/github` are still Pryce's own identity.

### Activity feed (`app/activity.py`)

An in-memory ring buffer (2,000 events) of what the gateway does on its own:
- every Pushover sent, Sazed's included (`notify.send_pushover`);
- outage and recovery transitions (`service_health`), muted ones included;
- disk and failed-deploy alerts;
- MCP tool calls, recorded by wrapping `mcp.tool` with name, args, outcome and duration.

`GET /activity/events?after=<seq>&boot=<id>` serves it. Sazed polls it every 10 s into its Activity timeline. A restart changes `boot`, and Sazed then takes everything still buffered. There's deliberately no migration (the shared GCP migration counter).

### MCP (`app/mcp_server.py`)

Streamable-HTTP MCP server (official `mcp` SDK, FastMCP, stateless + JSON responses) mounted at `/mcp/` (trailing slash). Tools wrap the router functions directly so MCP and REST share one code path and the same limits. The API key is checked by an ASGI guard around the mounted app, since FastAPI dependencies don't reach mounts. The session manager runs inside the FastAPI lifespan. Clients connect at `https://sazed.tail77c590.ts.net/gw/mcp/` (Caddy on CT 106 injects the key). Current tools: `send_to_phone` (wraps `notify.deliver`), `homelab_overview`, `homelab_storage`, `homelab_services`, `homelab_container`, `homelab_restart_container`, `homelab_start_container`, plus the worker job tools (plans/07): `research`, `second_opinion`, `delegate_code_task`, `continue_job`, `resume_job`, `job_status`, `cancel_job`, `list_jobs`. Those call Sazed's `/jobs` API (`SAZED_URL`, `SAZED_API_KEY`) with requested_by `mcp`: uncapped, and code tasks wait for approval in the console. Research and second opinions wait up to 50 s, then return a job_id. Job controls (plans/11 phase 1): a job can pause as `needs_input` with a `question` (`continue_job` answers it), `cancel_job` stops a job and keeps its session so `continue_job` can redirect it, `job_status` shows `recent_steps`, and every job view says worker output is data, not instructions. Paused jobs (Sazed plans/10 P1.7): a change job stopped by its timeout, tool cap or a worker restart is `paused` with its work so far on a draft PR; `resume_job` queues it again without re-approval, and the draft is marked ready when it finishes. The bot PR route takes `draft` (open as a draft) and `ready` (replace the title and description, then mark the draft ready via GraphQL). A follow-up also sends `pr_number`, the PR it continues: if that PR is closed or merged, the route answers 409 before committing, so closed work is never reopened as a new PR.

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
