# API Gateway Implementation Plan

Personal API gateway built with FastAPI (Python). Centralized entry point for personal services with notification management.

## Architecture Overview

```
api-gateway/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, CORS, lifespan
│   ├── config.py               # Settings via pydantic-settings
│   ├── dependencies.py         # Shared dependencies (auth, clients)
│   └── routers/
│       ├── __init__.py
│       ├── health.py           # /health - status & API directory
│       ├── notify.py           # /notify - Pushover notifications
│       ├── ai.py               # /ai - AI API gateway (placeholder)
│       ├── calendar.py         # /calendar - Google Calendar → Outlook
│       ├── tasks.py            # /tasks - Google Tasks → Todoist
│       ├── email.py            # /email - Gmail → Outlook
│       └── storage.py          # /storage - Drive/Photos → Homelab
├── .env.example                # Template for secrets
├── .gitignore
├── pyproject.toml              # Poetry dependencies
├── poetry.lock
├── README.md
└── Dockerfile                  # For GCP Cloud Run
```

## Tech Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Framework | FastAPI | Async, automatic OpenAPI docs, Pydantic validation |
| Config | pydantic-settings | Type-safe env loading, `.env` support |
| HTTP Client | httpx | Async HTTP for external APIs |
| Deps | Poetry | Lock files, virtual env management, modern Python standard |
| Container | Docker | Required for Cloud Run, reproducible builds |
| Deployment | GCP Cloud Run | Serverless, auto-scaling, simple deploy |

---

## Endpoints

### `/health`
- `GET /health` - Gateway status + list of available endpoints
- Returns service versions, uptime, endpoint directory

### `/notify`
- `POST /notify` - Send notification via Pushover
- Request body: `{ "title": str, "message": str, "priority": int }`
- Used internally by other endpoints for alerts

### `/ai` (placeholder)
- Stub router only - full implementation imported later
- Will proxy to Claude, ChatGPT, OpenRouter, DeepSeek

### `/calendar`
- `GET /calendar/auth` - Initiate OAuth flow
- `GET /calendar/callback` - OAuth callback
- `GET /calendar/events` - List events
- `GET /calendar/today` - Today's events
- `POST /calendar/events` - Create event
- Initial: Google Calendar | Future: Outlook, Apple Calendar

### `/tasks`
- `GET /tasks/lists` - Get task lists
- `GET /tasks/lists/{id}/tasks` - Get tasks
- `POST /tasks/lists/{id}/tasks` - Create task
- `PATCH /tasks/lists/{id}/tasks/{id}` - Update task
- `DELETE /tasks/lists/{id}/tasks/{id}` - Delete task
- Initial: Google Tasks | Future: Todoist, Notion

### `/email`
- `GET /email/auth` - Initiate OAuth flow
- `GET /email/callback` - OAuth callback
- `GET /email/unread` - Unread count + summaries
- `GET /email/messages` - List messages
- `GET /email/messages/{id}` - Get message
- `POST /email/send` - Send email
- Initial: Gmail | Future: Outlook, SMTP

### `/storage`
- `GET /storage/auth` - Initiate OAuth flow
- `GET /storage/callback` - OAuth callback
- `GET /storage/files` - List files (Drive)
- `GET /storage/files/{id}` - Get file
- `POST /storage/files` - Upload file
- `GET /storage/photos` - List photos
- `GET /storage/photos/albums` - List albums
- Initial: Google Drive + Photos | Future: Homelab NAS, S3

---

## Authentication Strategy

| Service | Auth Type | Storage |
|---------|-----------|---------|
| Pushover | API Key | `.env` |
| Google (all) | OAuth 2.0 | Token store (SQLite) |
| AI APIs | API Keys | `.env` |

> [!NOTE]
> **Single-user design.** OAuth tokens stored in SQLite locally. Production deployment will migrate to GCP Secret Manager (native Cloud Run integration, free tier covers personal use).

---

## Environment Variables

```env
# App
DEBUG=false
ALLOWED_ORIGINS=http://localhost:3000

# Pushover
PUSHOVER_USER_KEY=
PUSHOVER_API_TOKEN=

# Google OAuth (shared across calendar, tasks, email, storage)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=

# AI
ANTHROPIC_API_KEY=
```

---

## GCP Deployment

Target: **Cloud Run** (serverless container)

1. Dockerfile with uvicorn
2. Deploy via `gcloud run deploy`
3. Secrets via GCP Secret Manager (mounted as env vars)
4. Custom domain optional

---

## Implementation Phases

### Phase 1: Scaffolding
- [x] Implementation plan
- [x] Project structure + `main.py`
- [x] Config/settings module
- [x] Router stubs for all endpoints
- [x] `.env.example`, `.gitignore`
- [x] `pyproject.toml` (Poetry)
- [x] `README.md`
- [x] `Dockerfile`

### Phase 2: Core Endpoints
- [ ] `/health` endpoint with directory
- [ ] `/notify` Pushover integration
- [ ] Notification helper for cross-endpoint use

### Phase 3: Google OAuth
- [ ] Shared OAuth 2.0 flow (one token for all Google services)
- [ ] Token storage (SQLite)
- [ ] Token refresh handling

### Phase 4: Calendar, Tasks, Email, Storage
- [ ] Implement each endpoint against Google APIs
- [ ] Rate limiting considerations

### Phase 5: AI Gateway
- [ ] Import existing implementation
- [ ] Unified interface for multiple providers

---

## Design Decisions (Confirmed)

| Decision | Choice | Notes |
|----------|--------|-------|
| Token Storage | SQLite → GCP Secret Manager | SQLite for local dev, Secret Manager for Cloud Run (free tier, native integration) |
| User Model | Single-user | Personal gateway, no auth layer needed |
| Dependency Management | Poetry | Lock files, `pyproject.toml`, cleaner Dockerfile |
| Endpoint Abstraction | Domain-based | `/calendar`, `/email` instead of `/google` - extensible to other providers |

---

## Verification Plan

### Automated
- `poetry run uvicorn app.main:app --reload` - Dev server starts without errors
- `curl http://localhost:8000/health` - Returns 200 with endpoint list
- `curl http://localhost:8000/docs` - OpenAPI docs accessible

### Manual
- Review generated OpenAPI schema
- Verify `.env` loading works correctly
