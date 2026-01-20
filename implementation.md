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
│       ├── finance.py          # /finance - Plaid integration
│       └── google.py           # /google - Google services gateway
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

### `/finance`
- `POST /finance/link/token` - Create Plaid Link token
- `POST /finance/link/exchange` - Exchange public token for access token
- `GET /finance/accounts` - List connected accounts
- `GET /finance/transactions` - Fetch transactions
- OAuth flow with Plaid (sandbox initially)

### `/google`
- `GET /google/auth` - Initiate OAuth flow
- `GET /google/callback` - OAuth callback handler
- Sub-endpoints per service:
  - `/google/calendar/*` - Calendar operations
  - `/google/tasks/*` - Tasks operations
  - `/google/gmail/*` - Gmail operations
  - `/google/drive/*` - Drive operations
  - `/google/photos/*` - Photos operations
- OAuth 2.0 with Google APIs

---

## Authentication Strategy

| Service | Auth Type | Storage |
|---------|-----------|---------|
| Pushover | API Key | `.env` |
| Plaid | API Keys + OAuth | `.env` + token store |
| Google | OAuth 2.0 | Token store (file/db) |
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

# Plaid
PLAID_CLIENT_ID=
PLAID_SECRET=
PLAID_ENV=sandbox

# Google OAuth
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=

# AI (placeholder)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
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

### Phase 1: Scaffolding (Current)
- [x] Implementation plan
- [ ] Project structure + `main.py`
- [ ] Config/settings module
- [ ] Router stubs for all endpoints
- [ ] `.env.example`, `.gitignore`
- [ ] `pyproject.toml` (Poetry)
- [ ] `README.md`
- [ ] `Dockerfile`

### Phase 2: Core Endpoints
- [ ] `/health` endpoint with directory
- [ ] `/notify` Pushover integration
- [ ] Notification helper for cross-endpoint use

### Phase 3: Finance (Plaid)
- [ ] Plaid client setup
- [ ] Link token flow
- [ ] Account/transaction retrieval
- [ ] Webhook handling (optional)

### Phase 4: Google Integration
- [ ] OAuth 2.0 flow
- [ ] Token refresh handling
- [ ] Service-specific sub-routers
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

---

## Verification Plan

### Automated
- `uvicorn app.main:app --reload` - Dev server starts without errors
- `curl http://localhost:8000/health` - Returns 200 with endpoint list
- `curl http://localhost:8000/docs` - OpenAPI docs accessible

### Manual
- Review generated OpenAPI schema
- Verify `.env` loading works correctly
