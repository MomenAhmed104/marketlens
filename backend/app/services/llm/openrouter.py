"""OpenRouter chat-completions client with strict structured output.

Docs: https://openrouter.ai/docs/api-reference/chat-completion and
/docs/features/structured-outputs. Usage and cost are returned automatically.

`temperature` and `reasoning` are sent only when configured: with
`provider.require_parameters` OpenRouter refuses to route a request whose
parameters the model does not support (e.g. reasoning models and temperature).
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.services.llm.base import (
    LLMClient,
    LLMOutputError,
    LLMResult,
    LLMUsage,
    T,
    strict_json_schema,
)
from app.services.search.http import ProviderHttpClient

MAX_ERROR_CHARS = 1500


class OpenRouterClient(LLMClient):
    def __init__(
        self,
        http: ProviderHttpClient,
        api_key: str,
        model: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        temperature: float | None = None,
        max_completion_tokens: int = 6000,
        reasoning_effort: str | None = None,
        timeout_s: float = 90.0,
        repair_attempts: int = 1,
    ):
        self.http = http
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"authorization": f"Bearer {api_key}", "content-type": "application/json"}
        self.temperature = temperature
        self.max_completion_tokens = max_completion_tokens
        self.reasoning_effort = reasoning_effort
        self.timeout_s = timeout_s
        self.repair_attempts = repair_attempts

    async def aclose(self) -> None:
        await self.http.aclose()

    def _body(self, messages: list[dict], output_model: type[T], schema_name: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": self.max_completion_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": strict_json_schema(output_model)},
            },
            # Only route to endpoints that support every parameter (incl. structured output).
            "provider": {"require_parameters": True},
        }
        if self.temperature is not None:  # explicit overrides (including 0) are kept
            body["temperature"] = self.temperature
        if self.reasoning_effort is not None:
            # exclude: reasoning text is not returned (it is still generated and billed).
            body["reasoning"] = {"effort": self.reasoning_effort, "exclude": True}
        return body

    async def complete_structured(
        self, *, system: str, user: str, output_model: type[T], schema_name: str
    ) -> LLMResult[T]:
        messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        usage = LLMUsage()
        request_ids: list[str] = []
        started = time.perf_counter()
        model_used = self.model
        last_error = ""
        for attempt in range(1, self.repair_attempts + 2):
            data, _ = await self.http.request_json(
                "POST", self._url, headers=self._headers, timeout_s=self.timeout_s,
                json=self._body(messages, output_model, schema_name),
            )  # fmt: skip
            if data.get("error"):
                err = data["error"]
                raise LLMOutputError("openrouter", f"Provider error: {str(err.get('message', err))[:300]}",
                                     usage=usage, request_ids=request_ids)  # fmt: skip
            request_ids.append(str(data.get("id", "")))
            model_used = data.get("model") or model_used
            call_usage = _usage(data.get("usage") or {})
            usage.add(call_usage)
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            if choice.get("finish_reason") == "length":
                # Never parse or "repair" a cut-off reply: a partial JSON object could
                # validate with fields missing, and a retry would hit the same budget.
                raise LLMOutputError("openrouter", _truncation_message(content, call_usage,
                                     self.max_completion_tokens), usage=usage, request_ids=request_ids)  # fmt: skip
            try:
                value = output_model.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = str(e)[:MAX_ERROR_CHARS]
                messages += [
                    {"role": "assistant", "content": content[:20000]},
                    {"role": "user", "content": "Your previous reply did not match the required JSON schema. "
                                                f"Errors:\n{last_error}\nReturn only the corrected JSON."},
                ]  # fmt: skip
                continue
            return LLMResult(
                value=value, model=model_used, request_ids=request_ids,
                latency_ms=(time.perf_counter() - started) * 1000, usage=usage, attempts=attempt,
            )  # fmt: skip
        raise LLMOutputError("openrouter", f"Output failed schema validation after repair: {last_error[:300]}",
                             usage=usage, request_ids=request_ids)  # fmt: skip


def _usage(u: dict[str, Any]) -> LLMUsage:
    cost = u.get("cost")
    details = u.get("completion_tokens_details") or {}
    return LLMUsage(
        prompt_tokens=int(u.get("prompt_tokens") or 0),
        completion_tokens=int(u.get("completion_tokens") or 0),
        cost_usd=Decimal(str(cost)) if cost is not None else None,
        reasoning_tokens=int(details.get("reasoning_tokens") or 0),
    )


def _truncation_message(content: str, usage: LLMUsage, max_tokens: int) -> str:
    tokens = f"{usage.completion_tokens} completion tokens ({usage.reasoning_tokens} reasoning) of {max_tokens}"
    if not content.strip():
        return (f"Output budget used up before any answer (finish_reason=length, {tokens}); "
                "raise LLM1_/LLM2_MAX_COMPLETION_TOKENS or lower LLM1_/LLM2_REASONING_EFFORT")  # fmt: skip
    return f"Output truncated (finish_reason=length, {tokens}); raise LLM1_/LLM2_MAX_COMPLETION_TOKENS"
