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
from app.stage9.context import active_meter


def _build(settings: Settings, model: str | None, model_var: str, temperature: float | None, max_tokens: int,
           reasoning_effort: str | None, client: httpx.AsyncClient | None) -> LLMClient:  # fmt: skip
    key = settings.openrouter_api_key.get_secret_value().strip() if settings.openrouter_api_key else ""
    if not key:
        raise LLMNotConfiguredError("openrouter", "OPENROUTER_API_KEY is not set")
    model = (model or "").strip()
    if not model:
        raise LLMNotConfiguredError("openrouter", f"{model_var} is not set")
    meter = active_meter(settings, "openrouter")  # Stage 9: metered inside a budgeted run, refused outside one
    http = ProviderHttpClient("openrouter", client or httpx.AsyncClient(), max_retries=settings.llm_max_retries)
    if meter is not None:
        meter.attach(http, "openrouter")
    return OpenRouterClient(
        http, key, model,
        base_url=settings.openrouter_base_url,
        temperature=temperature,
        max_completion_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        timeout_s=settings.llm_timeout_seconds,
    )  # fmt: skip


def build_llm1(settings: Settings, client: httpx.AsyncClient | None = None) -> LLMClient:
    """LLM 1 (competitor discovery), configured independently of LLM 2."""
    return _build(settings, settings.llm1_model, "LLM1_MODEL", settings.llm1_temperature,
                  settings.llm1_max_completion_tokens, settings.llm1_reasoning_effort, client)  # fmt: skip


def build_llm2(settings: Settings, client: httpx.AsyncClient | None = None) -> LLMClient:
    """LLM 2 (product intelligence / extraction), configured independently of LLM 1."""
    return _build(settings, settings.llm2_model, "LLM2_MODEL", settings.llm2_temperature,
                  settings.llm2_max_completion_tokens, settings.llm2_reasoning_effort, client)  # fmt: skip


__all__ = [
    "LLMClient",
    "LLMNotConfiguredError",
    "LLMOutputError",
    "LLMResult",
    "LLMUsage",
    "OpenRouterClient",
    "build_llm1",
    "build_llm2",
    "strict_json_schema",
]
