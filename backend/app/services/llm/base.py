"""Provider-neutral LLM interface used by the agents."""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.services.search.http import ProviderError, ProviderNotConfiguredError

T = TypeVar("T", bound=BaseModel)


class LLMNotConfiguredError(ProviderNotConfiguredError):
    pass


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0  # includes reasoning tokens
    # OpenRouter reports cost in credits (USD-denominated). None if not reported.
    cost_usd: Decimal | None = None
    reasoning_tokens: int = 0  # part of completion_tokens; 0 if not reported

    def add(self, other: "LLMUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.reasoning_tokens += other.reasoning_tokens
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or Decimal(0)) + other.cost_usd


class LLMOutputError(ProviderError):
    """The model's output could not be turned into the requested schema.

    `usage` holds what the failed call(s) consumed, so callers can still
    account for the cost (e.g. a reply truncated after spending its budget).
    """

    def __init__(self, provider: str, message: str, status_code: int | None = None, *,
                 usage: LLMUsage | None = None, request_ids: list[str] | None = None):  # fmt: skip
        super().__init__(provider, message, status_code)
        self.usage = usage
        self.request_ids = request_ids or []


@dataclass
class LLMResult(Generic[T]):
    value: T
    model: str
    request_ids: list[str]
    latency_ms: float
    usage: LLMUsage
    attempts: int = 1
    warnings: list[str] = field(default_factory=list)


class LLMClient(ABC):
    """Returns validated Pydantic objects, never raw text."""

    model: str

    @abstractmethod
    async def complete_structured(
        self, *, system: str, user: str, output_model: type[T], schema_name: str
    ) -> LLMResult[T]: ...

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


# Keywords strict structured-output modes may reject. Pydantic still enforces
# them locally after parsing, so dropping them from the sent schema is safe.
_UNSUPPORTED_KEYWORDS = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
    "pattern", "format", "minItems", "maxItems", "default", "title", "uniqueItems",
}  # fmt: skip


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema for strict structured output.

    Every object gets `additionalProperties: false` and lists all of its
    properties as required (nullable fields stay nullable via anyOf).
    """
    schema = copy.deepcopy(model.model_json_schema())
    name_maps = {"properties", "$defs", "definitions"}  # keys here are names, not keywords

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(v) for v in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in name_maps and isinstance(v, dict):
                out[k] = {name: walk(sub) for name, sub in v.items()}
            elif k not in _UNSUPPORTED_KEYWORDS:
                out[k] = walk(v)
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out

    return walk(schema)
