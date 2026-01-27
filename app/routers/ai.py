"""AI API gateway - OpenAI-compatible endpoints."""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings
from app.providers import AnthropicProvider, OpenRouterProvider
from app.providers.base import (
    ChatCompletionRequest,
    ChatMessage,
    ModelInfo,
)


logger = logging.getLogger(__name__)
router = APIRouter()

limiter = Limiter(key_func=get_remote_address)

_anthropic: AnthropicProvider | None = None
_openrouter: OpenRouterProvider | None = None


def _get_anthropic() -> AnthropicProvider | None:
    global _anthropic
    if _anthropic is None and settings.anthropic_api_key:
        _anthropic = AnthropicProvider()
    return _anthropic


def _get_openrouter() -> OpenRouterProvider | None:
    global _openrouter
    if _openrouter is None and settings.openrouter_api_key:
        _openrouter = OpenRouterProvider()
    return _openrouter


def _get_provider_for_model(model: str):
    if model.startswith("claude-"):
        provider = _get_anthropic()
        if provider:
            return provider
        provider = _get_openrouter()
        if provider:
            return provider, f"anthropic/{model}"
        raise HTTPException(503, "No provider configured for Claude models")

    provider = _get_openrouter()
    if provider:
        return provider
    raise HTTPException(503, "OpenRouter not configured")


class ChatRequest(BaseModel):
    model: str | None = Field(None, description="Model to use")
    messages: list[ChatMessage]
    stream: bool = False
    max_tokens: int | None = Field(None, ge=1, le=128000)
    temperature: float | None = Field(None, ge=0, le=2)


class ModelsResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


@router.get("")
async def ai_status():
    providers = []
    if settings.anthropic_api_key:
        providers.append("anthropic")
    if settings.openrouter_api_key:
        providers.append("openrouter")

    return {
        "status": "ok" if providers else "no providers configured",
        "providers": providers,
        "default_model": settings.default_ai_model,
    }


@router.get("/v1/models", response_model=ModelsResponse)
async def list_models():
    models = []

    anthropic = _get_anthropic()
    if anthropic:
        models.extend(anthropic.get_models())

    openrouter = _get_openrouter()
    if openrouter:
        models.extend(openrouter.get_models())

    return ModelsResponse(data=models)


@router.post("/v1/chat/completions")
@limiter.limit("60/minute")
async def chat_completions(request: Request, chat_request: ChatRequest):
    model = chat_request.model or settings.default_ai_model

    if not chat_request.messages:
        raise HTTPException(400, "messages is required")

    result = _get_provider_for_model(model)

    if isinstance(result, tuple):
        provider, model = result
    else:
        provider = result

    internal_request = ChatCompletionRequest(
        model=model,
        messages=chat_request.messages,
        stream=chat_request.stream,
        max_tokens=chat_request.max_tokens,
        temperature=chat_request.temperature,
    )

    try:
        if chat_request.stream:
            return StreamingResponse(
                provider.chat_stream(internal_request),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
        else:
            response = await provider.chat(internal_request)
            return response.model_dump()

    except Exception as e:
        logger.error(f"AI request failed: {e}")
        raise HTTPException(502, f"Provider error: {str(e)}")
