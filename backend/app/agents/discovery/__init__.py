"""LLM 1: competitor discovery."""

from __future__ import annotations

from app.agents.discovery.agent import CompetitorDiscoveryAgent
from app.core.config import Settings
from app.services.catalogue import Catalogue
from app.services.llm import LLMClient, build_llm1
from app.services.search import get_configured_provider


def llm1_disabled_reason(settings: Settings) -> str | None:
    """None if LLM 1 can be used; otherwise a UI-safe reason (never a key value)."""
    if not settings.discovery_use_llm:
        return "DISCOVERY_USE_LLM is false"
    key = settings.openrouter_api_key.get_secret_value().strip() if settings.openrouter_api_key else ""
    missing = [name for name, ok in (("OPENROUTER_API_KEY", bool(key)), ("LLM1_MODEL", bool(settings.llm1_model)))
               if not ok]  # fmt: skip
    return f"{' and '.join(missing)} not set" if missing else None


async def build_discovery_agent(settings: Settings, catalogue: Catalogue) -> CompetitorDiscoveryAgent:
    """Agent wired to the configured search provider and, when configured, LLM 1.

    Missing LLM configuration selects LLM_DISABLED mode; it is never an error.
    A missing search provider or key raises ProviderNotConfiguredError, because
    discovery cannot run without search. Callers must `await agent.aclose()`.
    """
    search = get_configured_provider(settings)
    reason = llm1_disabled_reason(settings)
    llm: LLMClient | None = None
    if reason is None:
        try:
            llm = build_llm1(settings)
        except Exception:
            await search.aclose()
            raise
    return CompetitorDiscoveryAgent(
        search, llm, settings, catalogue.competitors,
        allowed_categories=[p.category for p in catalogue.products.values()],
        llm_disabled_reason=reason,
    )  # fmt: skip


__all__ = ["CompetitorDiscoveryAgent", "build_discovery_agent", "llm1_disabled_reason"]
