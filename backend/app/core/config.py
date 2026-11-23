"""Application settings, loaded from environment variables / backend/.env.

Secrets (API keys) must only ever come from the environment and are held as
SecretStr. See backend/.env.example for every supported variable.
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# OpenRouter's unified `reasoning.effort` values. Unset = the model's default.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_CATALOGUE_PATH = (
    REPO_ROOT / "data" / "catalogue" / "Stompbox_100_Product_Catalogue_No_Sale_Pricing.xlsx"
)
DEFAULT_REVIEW_DB_PATH = REPO_ROOT / "data" / "review" / "competitor_review.sqlite3"
DEFAULT_IMAGE_CACHE_DIR = REPO_ROOT / "data" / "image_cache"
DEFAULT_CATALOGUE_IMAGES_PATH = REPO_ROOT / "data" / "catalogue" / "product_images.json"


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
    # Unset = not sent (some models, e.g. reasoning models, reject `temperature`
    # and OpenRouter then finds no endpoint). An explicit value, including 0, is sent.
    llm1_temperature: float | None = Field(default=None, ge=0, le=2)
    llm1_max_completion_tokens: int = Field(default=6000, gt=0)  # includes reasoning tokens
    llm1_reasoning_effort: ReasoningEffort | None = None  # unset = not sent
    # LLM 2: product intelligence (extraction). Configured independently of LLM 1;
    # no default model (a user decision).
    llm2_model: str | None = None
    llm2_temperature: float | None = Field(default=None, ge=0, le=2)
    llm2_max_completion_tokens: int = Field(default=3000, gt=0)  # includes reasoning tokens
    llm2_reasoning_effort: ReasoningEffort | None = None

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
    # The former auto-approval thresholds (DISCOVERY_AUTO_APPROVE_MIN_CONFIDENCE, ..._RELEVANCE,
    # ..._EVIDENCE_URLS, DISCOVERY_REJECT_MAX_RELEVANCE) were removed in Stage 8.1: scores are
    # informational and never gate eligibility. Old .env entries are ignored (extra="ignore").
    # Competitors removed by the user (e.g. PMT): excluded before classification and
    # never written to the review queue. Registrable domains.
    discovery_excluded_competitor_domains: tuple[str, ...] = ("pmtonline.co.uk",)
    # Registrable names of marketplaces (amazon.co.uk, ebay.com, ...). Never
    # auto-approved; a reviewer must classify them as 'marketplace'.
    marketplace_domain_labels: tuple[str, ...] = ("amazon", "ebay", "reverb", "etsy", "gumtree")

    # --- Competitor review (Phase 4). Persistent decisions; git-ignored SQLite file. ---
    review_db_path: Path = DEFAULT_REVIEW_DB_PATH

    # --- Product images (MarketLens). Free HTTP only: each retrieved competitor page's HTML is
    # read once to find its primary image (JSON-LD > og:image > product meta > gallery img).
    # Images are display-only and never decide a match. False: images are NOT_ATTEMPTED.
    product_images_enabled: bool = True
    image_fetch_timeout_seconds: float = Field(default=8, gt=0)  # per connect/read; see the total deadline
    # Total deadline for one image lookup (page, redirects, image attempts, verification) and for
    # one /api/images cache-miss fetch. A slow site can never hold a product run longer than this.
    image_lookup_deadline_seconds: float = Field(default=20, gt=0, le=300)
    image_concurrency: int = Field(default=4, ge=1, le=16)
    image_page_max_bytes: int = Field(default=3_000_000, ge=100_000)  # HTML read cap
    image_max_bytes: int = Field(default=8_000_000, ge=100_000)  # one image's byte cap
    image_cache_dir: Path = DEFAULT_IMAGE_CACHE_DIR  # git-ignored; the dashboard's image cache
    # Upper bound on the cache's total size; the oldest entries are removed beyond it.
    image_cache_max_bytes: int = Field(default=300_000_000, ge=1_000_000)
    # Politeness per host (page and image requests): requests in flight and the minimum gap
    # between request starts to one host. The catalogue collector and runs share these rules.
    image_per_host_concurrency: int = Field(default=2, ge=1, le=8)
    image_host_min_interval_seconds: float = Field(default=0.5, ge=0, le=30)
    # /api/images serves only image URLs the backend itself found (catalogue sidecar or a run in
    # memory), so it cannot be used as a general-purpose fetcher. Cached copies are still served.
    image_proxy_known_urls_only: bool = True
    catalogue_images_path: Path = DEFAULT_CATALOGUE_IMAGES_PATH  # sidecar written by the collect CLI

    # --- Product intelligence (Phase 5) ---
    # Use LLM 2 when OPENROUTER_API_KEY and LLM2_MODEL are set. False forces
    # LLM_DISABLED mode (search + page retrieval only, nothing extracted).
    intelligence_use_llm: bool = True
    # 1 = one combined query per domain (brand + model + name); 2 adds an exact "brand model" query.
    intelligence_queries_per_domain: int = Field(default=1, ge=1, le=2)
    intelligence_results_per_query: int = Field(default=5, ge=1, le=10)
    intelligence_max_pages_per_domain: int = Field(default=2, ge=1, le=5)
    intelligence_max_pages_per_product: int = Field(default=20, ge=1, le=60)
    # Characters of retrieved page text sent to LLM 2 (quotes are verified against this text).
    intelligence_page_chars: int = Field(default=12_000, ge=1_000, le=30_000)
    intelligence_search_concurrency: int = Field(default=4, ge=1, le=10)
    intelligence_llm_concurrency: int = Field(default=3, ge=1, le=10)
    intelligence_retrieval_batch_size: int = Field(default=10, ge=1, le=25)
    # Approved domains of these types are not searched for offers: a directory
    # lists other sellers, so its prices are not its own.
    intelligence_excluded_company_types: tuple[str, ...] = ("directory",)
    # Task A: a second LLM 2 call per retrieved single-product page that proposes a match
    # classification. Python makes the final decision; the proposal can only add uncertainty.
    intelligence_match_proposals: bool = True

    # --- Matching and price validation (Phase 6) ---
    # A VALID price retrieved longer ago than this is STALE and not compared.
    matching_price_max_age_hours: int = Field(default=24, ge=1, le=24 * 30)

    # --- Pipeline orchestration (Phase 7). Run state is in memory only. ---
    pipeline_max_products_per_run: int = Field(default=5, ge=1, le=10)
    pipeline_max_active_runs: int = Field(default=2, ge=1, le=10)
    pipeline_max_runs_retained: int = Field(default=50, ge=1, le=500)  # finished runs kept for retrieval
    pipeline_run_ttl_minutes: int = Field(default=120, ge=1)  # finished runs older than this are dropped
    pipeline_run_timeout_seconds: float = Field(default=1800, gt=0)
    pipeline_discovery_timeout_seconds: float = Field(default=300, gt=0)
    pipeline_product_timeout_seconds: float = Field(default=600, gt=0)  # search + retrieval + extraction, per product
    # Search automatically search-eligible discovered domains (rules.is_search_eligible): this
    # run's and those stored from earlier runs. False: only configured and reviewer-approved
    # domains are searched. The name predates Stage 8.1 and is kept for existing .env files.
    pipeline_search_auto_approved: bool = True
    # Cost control: at most this many automatically eligible domains are searched per run
    # (pipeline and product-intelligence API), ranked by grounded discovery relevance.
    # Configured and reviewer-approved domains do not count. Each extra domain costs about one
    # Exa search plus a few page retrievals and LLM 2 calls per selected product.
    max_auto_search_targets: int = Field(default=5, ge=0, le=50)
    pipeline_max_events_per_run: int = Field(default=5000, ge=100)
    pipeline_sse_heartbeat_seconds: float = Field(default=15, gt=0)

    @field_validator(
        "llm1_model", "llm2_model", "llm1_temperature", "llm2_temperature",
        "llm1_reasoning_effort", "llm2_reasoning_effort", mode="before",
    )  # fmt: skip
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        """`LLM1_MODEL=` or `LLM1_MODEL=   ` in .env means unset, not an empty model id."""
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
