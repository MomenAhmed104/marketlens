"""Application settings, loaded from environment variables / backend/.env.

Secrets (API keys) must only ever come from the environment. None are needed in
Phase 1; they are documented in backend/.env.example for later phases.
"""

from functools import lru_cache
from pathlib import Path

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
