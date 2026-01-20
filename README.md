# API Gateway

Personal API gateway built with FastAPI. Centralized entry point for notifications, calendar, tasks, email, storage, and AI APIs.

## Endpoints

| Route | Description | Provider |
|-------|-------------|----------|
| `/health` | Gateway status and API directory | - |
| `/notify` | Push notifications | Pushover |
| `/ai` | AI API gateway | Claude, ChatGPT, OpenRouter, DeepSeek |
| `/calendar` | Calendar events | Google Calendar → Outlook |
| `/tasks` | Task management | Google Tasks → Todoist |
| `/email` | Email access and sending | Gmail → Outlook |
| `/storage` | File and photo storage | Google Drive/Photos → Homelab |

## Setup

```bash
# Install dependencies
poetry install

# Copy env template and fill in secrets
cp .env.example .env

# Run dev server
poetry run uvicorn app.main:app --reload
```

## Deployment

Deploys to GCP Cloud Run via Docker.

```bash
# Build
docker build -t api-gateway .

# Run locally
docker run -p 8000:8000 --env-file .env api-gateway
```

## API Docs

Available at `/docs` (Swagger) or `/redoc` when running.
