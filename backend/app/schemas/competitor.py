"""Competitor company/domain schema."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.schemas.common import DomainApprovalStatus


class CompanyType(StrEnum):
    RETAILER = "retailer"
    SPECIALIST_RETAILER = "specialist_retailer"
    MANUFACTURER = "manufacturer"
    MARKETPLACE = "marketplace"
    # Price-comparison sites and shop directories: list other sellers, never a benchmark.
    DIRECTORY = "directory"
    OTHER = "other"


# Only these types can ever be price benchmarks (still only with an explicit benchmark flag; the
# separate UK gate clearance was retired on 19 Nov 2026). Manufacturers are included by business decision (18 Nov 2026): a
# manufacturer's own displayed price joins the main benchmark alongside retailers. A
# reference-only site such as Gibson stays out through its own price_benchmark=false.
# Marketplaces, directories and other types never qualify.
BENCHMARK_ELIGIBLE_TYPES = frozenset({CompanyType.RETAILER, CompanyType.SPECIALIST_RETAILER,
                                      CompanyType.MANUFACTURER})  # fmt: skip


class CompetitorPriority(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    REFERENCE_ONLY = "Reference only"


class CompetitorSource(StrEnum):
    CATALOGUE_WORKBOOK = "catalogue_workbook"
    DISCOVERED = "discovered"


class CompetitorDomain(BaseModel):
    model_config = ConfigDict(frozen=True)

    competitor_id: str | None = None
    name: str = Field(min_length=1)
    # Normalised hostname used as the deduplication key (lowercase, no "www.").
    domain: str = Field(min_length=3)
    homepage_url: HttpUrl
    company_type: CompanyType
    # Original classification text from the source, preserved verbatim.
    classification: str | None = None
    priority: CompetitorPriority | None = None
    # False for manufacturer reference sites (e.g. Gibson): their prices must not
    # be mixed into the retailer benchmark.
    price_benchmark: bool
    approval_status: DomainApprovalStatus
    source: CompetitorSource
    notes: str | None = None
    # Empty = no category restriction recorded. Not in the workbook yet.
    relevant_categories: list[str] = Field(default_factory=list)
