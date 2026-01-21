"""Base provider interface for AI clients."""

from abc import ABC, abstractmethod
from typing import AsyncIterator

from pydantic import BaseModel


class ChatMessage(BaseModel):
    """OpenAI-style chat message."""
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request."""
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None


class ChatCompletionChoice(BaseModel):
    """Single completion choice."""
    index: int
    message: ChatMessage
    finish_reason: str | None = None


class UsageInfo(BaseModel):
    """Token usage information."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: UsageInfo | None = None


class ModelInfo(BaseModel):
    """Model information."""
    id: str
    object: str = "model"
    created: int | None = None
    owned_by: str


class BaseProvider(ABC):
    """Abstract base class for AI providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier."""
        pass

    @abstractmethod
    async def chat(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Send chat completion request."""
        pass

    @abstractmethod
    async def chat_stream(self, request: ChatCompletionRequest) -> AsyncIterator[str]:
        """Stream chat completion response as SSE data."""
        pass

    @abstractmethod
    def get_models(self) -> list[ModelInfo]:
        """Return list of available models."""
        pass

    @abstractmethod
    def supports_model(self, model: str) -> bool:
        """Check if provider supports the given model."""
        pass
