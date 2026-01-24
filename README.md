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

### CI/CD (GitHub Actions)

Deploys on push to `main`. One-time GCP setup in the Cloud Console: create/select project, enable Cloud Run and Artifact Registry APIs, create Artifact Registry repo `api-gateway` in `us-central1`, create a service account with Cloud Run Admin, Artifact Registry Writer, and Service Account User, create a JSON key and add GitHub secrets `GCP_SA_KEY` and `GCP_PROJECT_ID`. Set the Cloud Run service env vars in the Console after the first deploy.

## API Docs

Available at `/docs` (Swagger) or `/redoc` when running.
