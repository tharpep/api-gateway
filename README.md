# API Gateway

Personal API gateway built with FastAPI. Centralized proxy for Google APIs, AI providers, and internal services.

## Ecosystem

Part of a personal AI ecosystem — see [sazed](https://github.com/tharpep/sazed) for the full picture.

| Related repo | How it uses this gateway |
|-------------|--------------------------|
| [sazed](https://github.com/tharpep/sazed) | All tool calls (calendar, tasks, email, storage, github, sheets, AI, KB) |
| [knowledge-base](https://github.com/tharpep/knowledge-base) | Drive file access via `/storage`, LLM calls via `/ai` |
| [automations](https://github.com/tharpep/automations) | Script integrations via `GATEWAY_URL` + `GATEWAY_API_KEY` |

## Endpoints

| Route | Description | Provider |
|-------|-------------|----------|
| `/health` | Gateway status | — |
| `/notify` | Push notifications | Pushover |
| `/ai` | AI API proxy (OpenAI-compatible) | Anthropic, OpenRouter |
| `/calendar` | Calendar CRUD | Google Calendar |
| `/tasks` | Task management | Google Tasks |
| `/email` | Email read + draft | Gmail |
| `/storage` | File storage (Drive KB subfolders) | Google Drive |
| `/kb` | Knowledge base proxy | knowledge-base service |
| `/search` | Web search + URL fetch | — |
| `/github` | GitHub repos, issues, PRs, code | GitHub API |
| `/sheets` | Google Sheets CRUD | Google Sheets |

## Setup

```bash
# Install dependencies
poetry install

# Copy env template and fill in secrets
cp .env.example .env

# Run dev server
poetry run uvicorn app.main:app --reload
```

## Authentication

When `API_KEY` is set, all routes except `/health` and `/docs` require a valid key:

- **Header:** `X-API-Key: <your-key>`
- **Header:** `Authorization: Bearer <your-key>`

If `API_KEY` is empty, no authentication is required (local dev).

## Deployment

Deploys to GCP Cloud Run via Docker on push to `main` (GitHub Actions).

```bash
docker build -t api-gateway .
docker run -p 8000:8000 --env-file .env api-gateway
```

One-time GCP setup: enable Cloud Run + Artifact Registry, create repo `api-gateway` in `us-central1`, create a service account with Cloud Run Admin + Artifact Registry Writer + Service Account User, add `GCP_SA_KEY` and `GCP_PROJECT_ID` secrets to GitHub. Set Cloud Run env vars in the Console after the first deploy.

## API Docs

Available at `/docs` (Swagger) or `/redoc` when running.
