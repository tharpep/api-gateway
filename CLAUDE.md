# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Personal API gateway built with **FastAPI** (Python 3.11+) that provides a unified interface for multiple external services: notifications (Pushover), Google Calendar, AI/LLM providers (Anthropic, OpenRouter), with stubs for tasks, email, storage, context aggregation, and webhooks.

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

Stateless OAuth helper with token refresh. Routers that use Google APIs (calendar, etc.) cache the access token in-memory and refresh on 401 responses. Initial refresh token obtained via `get_google_token.py` utility script.

### Routers (`app/routers/`)

Each router is a FastAPI `APIRouter` for a service domain. Implemented: `health`, `notify`, `ai`, `calendar`. Stubs: `tasks`, `email`, `storage`, `context`, `webhooks`.

## Key Conventions

- **Ruff** for linting and formatting: line length 100, target Python 3.11, rules: E, F, I, UP
- All external API calls use **httpx** async client
- Streaming AI responses use **Server-Sent Events** (SSE) format
- CORS allows `localhost:3000` and `localhost:3001` by default (configurable in settings)
- Docker uses `PORT` env var (Cloud Run sets 8080), falls back to 8000
