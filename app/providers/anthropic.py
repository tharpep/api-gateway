"""Anthropic Claude API provider."""

import json
import time
import uuid
from typing import AsyncIterator

import httpx

from app.config import settings
from .base import (
    BaseProvider,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChoice,
    ChatMessage,
    UsageInfo,
    ModelInfo,
)


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

CLAUDE_MODELS = [
    "claude-opus-4-5-20251101",
    "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5-20251001",
]


class AnthropicProvider(BaseProvider):
    """Anthropic Claude API client."""

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def _get_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def _convert_messages(self, messages: list[ChatMessage]) -> tuple[str | None, list[dict]]:
        """Convert OpenAI messages to Anthropic format."""
        system_content = None
        anthropic_messages = []

        for msg in messages:
            if msg.role == "system":
                system_content = msg.content
            else:
                role = msg.role if msg.role in ("user", "assistant") else "user"
                anthropic_messages.append({"role": role, "content": msg.content})

        return system_content, anthropic_messages

    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        system_content, messages = self._convert_messages(request.messages)

        payload = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 1024,
        }

        if system_content:
            payload["system"] = system_content

        if request.temperature is not None:
            payload["temperature"] = request.temperature

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                ANTHROPIC_API_URL,
                headers=self._get_headers(),
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = ""
        if data.get("content") and len(data["content"]) > 0:
            content = data["content"][0].get("text", "")

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
            created=int(time.time()),
            model=request.model,
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=content),
                    finish_reason=data.get("stop_reason", "stop"),
                )
            ],
            usage=UsageInfo(
                prompt_tokens=data.get("usage", {}).get("input_tokens", 0),
                completion_tokens=data.get("usage", {}).get("output_tokens", 0),
                total_tokens=(
                    data.get("usage", {}).get("input_tokens", 0)
                    + data.get("usage", {}).get("output_tokens", 0)
                ),
            ),
        )

    async def chat_stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        system_content, messages = self._convert_messages(request.messages)

        payload = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 1024,
            "stream": True,
        }

        if system_content:
            payload["system"] = system_content

        if request.temperature is not None:
            payload["temperature"] = request.temperature

        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                ANTHROPIC_API_URL,
                headers=self._get_headers(),
                json=payload,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]
                    if data_str == "[DONE]":
                        yield "data: [DONE]\n\n"
                        break

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            chunk = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": request.model,
                                "choices": [{
                                    "index": 0,
                                    "delta": {"content": text},
                                    "finish_reason": None,
                                }],
                            }
                            yield f"data: {json.dumps(chunk)}\n\n"

                    elif event.get("type") == "message_stop":
                        chunk = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": request.model,
                            "choices": [{
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                            }],
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
                        yield "data: [DONE]\n\n"

    def get_models(self) -> list[ModelInfo]:
        return [ModelInfo(id=model, owned_by="anthropic") for model in CLAUDE_MODELS]

    def supports_model(self, model: str) -> bool:
        return model.startswith("claude-")
