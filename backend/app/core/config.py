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


@lru_cache
def get_settings() -> Settings:
    return Settings()
