"""OpenRouter API provider for multi-model access."""

import time
import uuid
from typing import AsyncIterator

import httpx

from app.config import settings

from .base import (
    BaseProvider,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ModelInfo,
    UsageInfo,
)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

OPENROUTER_MODELS = [
    ("openai/gpt-4o", "openai"),
]


class OpenRouterProvider(BaseProvider):
    """OpenRouter API client."""

    @property
    def provider_name(self) -> str:
        return "openrouter"

    def _get_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "HTTP-Referer": "https://api-gateway.local",
            "X-Title": "API Gateway",
        }

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        payload = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }

        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        if request.temperature is not None:
            payload["temperature"] = request.temperature

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                OPENROUTER_API_URL,
                headers=self._get_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        choices = []
        for i, choice in enumerate(data.get("choices", [])):
            message = choice.get("message", {})
            choices.append(
                ChatCompletionChoice(
                    index=i,
                    message=ChatMessage(
                        role=message.get("role", "assistant"),
                        content=message.get("content", ""),
                    ),
                    finish_reason=choice.get("finish_reason", "stop"),
                )
            )

        usage_data = data.get("usage", {})
        usage = UsageInfo(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        ) if usage_data else None

        return ChatCompletionResponse(
            id=data.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            created=data.get("created", int(time.time())),
            model=request.model,
            choices=choices,
            usage=usage,
        )

    async def chat_stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        payload = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": True,
        }

        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens

        if request.temperature is not None:
            payload["temperature"] = request.temperature

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                OPENROUTER_API_URL,
                headers=self._get_headers(),
                json=payload,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            yield "data: [DONE]\n\n"
                            break
                        yield f"data: {data_str}\n\n"
                    else:
                        yield f"{line}\n"

    def get_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=model_id, owned_by=owner) for model_id, owner in OPENROUTER_MODELS]

    def supports_model(self, model: str) -> bool:
        known_prefixes = ("openai/", "deepseek/", "mistralai/", "google/", "meta-llama/", "anthropic/")
        return any(model.startswith(prefix) for prefix in known_prefixes)
