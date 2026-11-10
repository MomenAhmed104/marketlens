"""Search provider abstraction.

Use `build_provider(name, settings)` or `get_configured_provider(settings)`;
the rest of the application depends only on `SearchProvider` and the schemas
in `app.schemas.search`.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.services.search.base import SearchProvider
from app.services.search.brave import BraveProvider
from app.services.search.exa import ExaProvider
from app.services.search.http import ProviderHttpClient, ProviderNotConfiguredError
from app.services.search.tavily import TavilyProvider

PROVIDER_NAMES = ("exa", "tavily", "brave")


def _key(settings: Settings, name: str) -> str:
    field = {"exa": "exa_api_key", "tavily": "tavily_api_key", "brave": "brave_search_api_key"}[name]
    secret = getattr(settings, field)
    if secret is None or not secret.get_secret_value().strip():
        raise ProviderNotConfiguredError(name, f"{field.upper()} is not set")
    return secret.get_secret_value().strip()


def configured_providers(settings: Settings) -> list[str]:
    out = []
    for name in PROVIDER_NAMES:
        try:
            _key(settings, name)
            out.append(name)
        except ProviderNotConfiguredError:
            pass
    return out


def build_provider(
    name: str, settings: Settings, client: httpx.AsyncClient | None = None
) -> SearchProvider:
    if name not in PROVIDER_NAMES:
        raise ValueError(f"Unknown search provider {name!r}; expected one of {PROVIDER_NAMES}")
    key = _key(settings, name)
    http = ProviderHttpClient(name, client or httpx.AsyncClient(), max_retries=settings.search_max_retries)
    if name == "exa":
        return ExaProvider(
            http, key,
            search_type=settings.exa_search_type,
            content_max_age_hours=settings.exa_content_max_age_hours,
            livecrawl_timeout_ms=settings.exa_livecrawl_timeout_ms,
            search_timeout_s=settings.search_timeout_seconds,
            content_timeout_s=settings.content_timeout_seconds,
        )  # fmt: skip
    if name == "tavily":
        return TavilyProvider(
            http, key,
            usd_per_credit=settings.tavily_usd_per_credit,
            search_depth=settings.tavily_search_depth,
            extract_depth=settings.tavily_extract_depth,
            search_timeout_s=settings.search_timeout_seconds,
            content_timeout_s=settings.content_timeout_seconds,
        )  # fmt: skip
    return BraveProvider(
        http, key,
        usd_per_1k_requests=settings.brave_usd_per_1k_requests,
        search_timeout_s=settings.search_timeout_seconds,
    )  # fmt: skip


def get_configured_provider(settings: Settings, client: httpx.AsyncClient | None = None) -> SearchProvider:
    if settings.search_provider is None:
        raise ProviderNotConfiguredError("search", "SEARCH_PROVIDER is not set")
    return build_provider(settings.search_provider, settings, client)


__all__ = [
    "PROVIDER_NAMES",
    "SearchProvider",
    "build_provider",
    "configured_providers",
    "get_configured_provider",
]
