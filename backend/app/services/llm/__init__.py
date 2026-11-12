"""LLM gateway. Agents depend on `LLMClient`; OpenRouter is the implementation."""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.services.llm.base import (
    LLMClient,
    LLMNotConfiguredError,
    LLMOutputError,
    LLMResult,
    LLMUsage,
    strict_json_schema,
)
from app.services.llm.openrouter import OpenRouterClient
from app.services.search.http import ProviderHttpClient


def build_llm1(settings: Settings, client: httpx.AsyncClient | None = None) -> LLMClient:
    """LLM 1 (competitor discovery), configured independently of LLM 2."""
    key = settings.openrouter_api_key.get_secret_value().strip() if settings.openrouter_api_key else ""
    if not key:
        raise LLMNotConfiguredError("openrouter", "OPENROUTER_API_KEY is not set")
    if not settings.llm1_model:
        raise LLMNotConfiguredError("openrouter", "LLM1_MODEL is not set")
    http = ProviderHttpClient("openrouter", client or httpx.AsyncClient(), max_retries=settings.llm_max_retries)
    return OpenRouterClient(
        http, key, settings.llm1_model,
        base_url=settings.openrouter_base_url,
        temperature=settings.llm1_temperature,
        max_completion_tokens=settings.llm1_max_completion_tokens,
        timeout_s=settings.llm_timeout_seconds,
    )  # fmt: skip


__all__ = [
    "LLMClient",
    "LLMNotConfiguredError",
    "LLMOutputError",
    "LLMResult",
    "LLMUsage",
    "OpenRouterClient",
    "build_llm1",
    "strict_json_schema",
]
