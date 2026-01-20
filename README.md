# API Gateway

Personal API gateway built with FastAPI. Centralized entry point for notifications, Google services, and AI APIs.

## Endpoints

| Route | Description |
|-------|-------------|
| `/health` | Gateway status and API directory |
| `/notify` | Pushover notification manager |
| `/ai` | AI API gateway (Claude, ChatGPT, OpenRouter, DeepSeek) |
| `/google` | Google services (Calendar, Tasks, Gmail, Drive, Photos) |

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
