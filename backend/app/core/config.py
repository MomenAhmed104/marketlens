"""Application settings, loaded from environment variables / backend/.env.

Secrets (API keys) must only ever come from the environment and are held as
SecretStr. See backend/.env.example for every supported variable.
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_CATALOGUE_PATH = (
    REPO_ROOT / "data" / "catalogue" / "Stompbox_100_Product_Catalogue_No_Sale_Pricing.xlsx"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Stompbox Competitor Intelligence"
    catalogue_path: Path = DEFAULT_CATALOGUE_PATH
    catalogue_products_sheet: str = "Products"
    catalogue_competitors_sheet: str = "Competitors"

    # --- Search providers (Phase 2). Keys are SecretStr so they never print. ---
    search_provider: Literal["exa", "tavily", "brave"] | None = None
    exa_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    brave_search_api_key: SecretStr | None = None

    search_timeout_seconds: float = 20.0
    content_timeout_seconds: float = 45.0
    search_max_retries: int = 2

    exa_search_type: str = "auto"
    # Accept cached content up to this age, otherwise live-crawl. None = Exa default.
    exa_content_max_age_hours: int | None = 24
    # Max wait for an Exa live crawl (ms). Smoke test: 36 s batches at 36000.
    exa_livecrawl_timeout_ms: int = Field(default=15_000, gt=0, le=90_000)
    tavily_search_depth: str = "basic"
    tavily_extract_depth: str = "basic"

    # List prices used only where a provider does not report cost. Checked
    # against the providers' public pricing pages on 2026-11-10.
    tavily_usd_per_credit: Decimal = Decimal("0.008")
    brave_usd_per_1k_requests: Decimal = Decimal("5")

    # --- LLM gateway (Phase 3+) ---
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_timeout_seconds: float = 90.0
    llm_max_retries: int = 2
    # LLM 1: competitor discovery. No default model: the choice is the user's.
    llm1_model: str | None = None
    llm1_temperature: float = Field(default=0.0, ge=0, le=2)
    llm1_max_completion_tokens: int = Field(default=6000, gt=0)

    # --- Competitor discovery (Phase 3) ---
    # Use LLM 1 when OPENROUTER_API_KEY and LLM1_MODEL are set. False forces
    # LLM_DISABLED mode (baseline queries, no classification) even if they are.
    discovery_use_llm: bool = True
    # Our own site(s): never a competitor.
    own_domains: tuple[str, ...] = ("stompbox.co.uk",)
    # Platforms that are not shops; excluded before classification.
    discovery_excluded_domains: tuple[str, ...] = (
        "youtube.com", "youtu.be", "reddit.com", "facebook.com", "instagram.com", "tiktok.com",
        "twitter.com", "x.com", "pinterest.com", "wikipedia.org", "quora.com", "google.com",
        "linkedin.com", "soundcloud.com", "spotify.com",
    )  # fmt: skip
    discovery_max_global_queries_per_product: int = Field(default=4, ge=1, le=10)
    discovery_results_per_query: int = Field(default=10, ge=1, le=20)
    discovery_max_candidates: int = Field(default=15, ge=1, le=50)
    discovery_profile_results: int = Field(default=3, ge=0, le=10)
    discovery_max_targeted_variants: int = Field(default=2, ge=1, le=5)
    discovery_classification_batch_size: int = Field(default=8, ge=1, le=20)
    discovery_search_concurrency: int = Field(default=3, ge=1, le=10)
    # Auto-approval thresholds. Starting values, NOT calibrated: tune on labelled runs.
    discovery_auto_approve_min_confidence: float = Field(default=0.85, ge=0, le=1)
    discovery_auto_approve_min_relevance: float = Field(default=0.75, ge=0, le=1)
    discovery_auto_approve_min_evidence_urls: int = Field(default=2, ge=1)
    discovery_reject_max_relevance: float = Field(default=0.3, ge=0, le=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
