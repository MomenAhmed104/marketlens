"""HTTP transport shared by search providers: timeouts, retries, rate limits.

Error messages never include request headers, so API keys cannot leak into
logs or API responses.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SearchProviderError(Exception):
    """Base class. `provider` and `status_code` are safe to show in the UI."""

    def __init__(self, provider: str, message: str, status_code: int | None = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")


class ProviderNotConfiguredError(SearchProviderError):
    pass


class ProviderAuthError(SearchProviderError):
    pass


class ProviderRateLimitError(SearchProviderError):
    pass


class ProviderUnavailableError(SearchProviderError):
    pass


class ProviderRequestError(SearchProviderError):
    pass


class RetrievalNotSupportedError(SearchProviderError):
    pass


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form: fall back to exponential backoff


class ProviderHttpClient:
    def __init__(
        self,
        provider: str,
        client: httpx.AsyncClient,
        *,
        max_retries: int = 2,
        backoff_base_s: float = 0.5,
        max_backoff_s: float = 10.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.provider = provider
        self._client = client
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.max_backoff_s = max_backoff_s
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout_s: float,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[Any, float]:
        """Return (decoded JSON, total wall-clock latency in ms including retries)."""
        started = time.perf_counter()
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method, url, headers=headers, json=json, params=params, timeout=timeout_s
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < self.max_retries:
                    await self._sleep(self._backoff(attempt))
                    attempt += 1
                    continue
                raise ProviderUnavailableError(
                    self.provider, f"{type(exc).__name__} after {attempt + 1} attempt(s)"
                ) from None

            status = response.status_code
            if status in RETRYABLE_STATUS and attempt < self.max_retries:
                delay = _retry_after_seconds(response) if status == 429 else None
                await self._sleep(min(delay if delay is not None else self._backoff(attempt),
                                      self.max_backoff_s))  # fmt: skip
                attempt += 1
                continue

            latency_ms = (time.perf_counter() - started) * 1000
            if status < 400:
                try:
                    return response.json(), latency_ms
                except ValueError:
                    raise ProviderRequestError(self.provider, "Response was not JSON", status) from None
            body = response.text[:300]
            if status in (401, 403):
                raise ProviderAuthError(self.provider, f"Authentication failed (HTTP {status})", status)
            if status == 429:
                raise ProviderRateLimitError(self.provider, "Rate limit exceeded", status)
            if status >= 500:
                raise ProviderUnavailableError(self.provider, f"HTTP {status}: {body}", status)
            raise ProviderRequestError(self.provider, f"HTTP {status}: {body}", status)

    def _backoff(self, attempt: int) -> float:
        return min(self.backoff_base_s * (2**attempt), self.max_backoff_s)


# The transport is shared by search and LLM providers; this is the neutral name.
ProviderError = SearchProviderError
