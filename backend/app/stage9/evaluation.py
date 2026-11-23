"""Stage 9 scenario and answer key: schema, loading and catalogue cross-checks.

The answer key is written BEFORE any live run and scored against afterwards. Every claim carries
an evidence label:

  VERIFIED    a fact from the catalogue workbook or a dated project record (benchmark, saved run);
              a historical listing is verified for its date only, never as a current listing;
  HYPOTHESIS  a reasonable expectation that a live run may contradict;
  UNKNOWN     needs human review; never scored as right or wrong.

Identity terms are matched on normalised text (lower case, every non-alphanumeric run turned
into one space, whole words): "RC-5" matches "boss-rc-5-loop" but not "rc-500".
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

DEFAULT_ANSWER_KEY = Path(__file__).resolve().parents[3] / "docs" / "stage9" / "answer-key.json"


class Label(StrEnum):
    VERIFIED = "VERIFIED"
    HYPOTHESIS = "HYPOTHESIS"
    UNKNOWN = "UNKNOWN"


class Claim(BaseModel):
    text: str
    label: Label
    source: str | None = None  # where a VERIFIED claim comes from (file, run id, date)

    @model_validator(mode="after")
    def _verified_needs_source(self) -> Claim:
        if self.label is Label.VERIFIED and not (self.source or "").strip():
            raise ValueError(f"VERIFIED claim without a source: {self.text!r}")
        return self


class TermGroup(BaseModel):
    """Satisfied when ANY of `any_of` appears (normalised, whole words)."""

    name: str  # brand, model, handedness, finish, designation, ...
    any_of: list[str] = Field(min_length=1)
    label: Label


class ExpectedOutcome(StrEnum):
    EXACT_LISTED = "EXACT_LISTED"  # the retailer lists this exact product
    VARIANT_ONLY = "VARIANT_ONLY"  # only other variants / conditions / configurations
    NOT_LISTED = "NOT_LISTED"  # no listing of this product
    UNKNOWN = "UNKNOWN"


class RetailerExpectation(BaseModel):
    domain: str
    expected: ExpectedOutcome
    label: Label
    basis: str  # why; historical evidence names its date and record


class ScenarioProduct(BaseModel):
    product_id: str
    catalogue_name: str  # exact workbook product_name
    brand: str
    model: str  # workbook manufacturer_model
    category: str
    variant: Claim | None = None
    catalogue_url: str
    specifications: str | None = None  # workbook text, verbatim; None when the workbook has none
    missing_or_ambiguous: list[Claim] = Field(default_factory=list)
    why_useful: str


class AnswerKeyEntry(BaseModel):
    product_id: str
    identity: Claim
    exact_requires: list[TermGroup]  # every group must be satisfied for an EXACT match
    not_exact_if: list[TermGroup] = Field(default_factory=list)  # any hit means it is NOT the exact product
    similar_examples: list[Claim] = Field(default_factory=list)
    non_match_distinctions: list[Claim] = Field(default_factory=list)
    retailers: list[RetailerExpectation] = Field(default_factory=list)
    price_scoring_eligible: bool
    price_scoring_note: str
    evidence_required: list[str]
    notes: list[Claim] = Field(default_factory=list)


class PlannedRun(BaseModel):
    name: str
    product_ids: list[str] = Field(min_length=1)
    run_discovery: bool = True
    note: str | None = None


class AnswerKey(BaseModel):
    version: int = 1
    status: str  # DRAFT until the user approves it
    prepared_at: str
    prepared_from: list[str]
    scenario: list[ScenarioProduct]
    planned_runs: list[PlannedRun]
    entries: list[AnswerKeyEntry]

    @model_validator(mode="after")
    def _consistent(self) -> AnswerKey:
        ids = [p.product_id for p in self.scenario]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate scenario product")
        if sorted(e.product_id for e in self.entries) != sorted(ids):
            raise ValueError("every scenario product needs exactly one answer-key entry")
        for r in self.planned_runs:
            if set(r.product_ids) - set(ids):
                raise ValueError(f"planned run {r.name} names products outside the scenario")
        return self

    def entry(self, product_id: str) -> AnswerKeyEntry | None:
        return next((e for e in self.entries if e.product_id == product_id), None)


def load_answer_key(path: Path = DEFAULT_ANSWER_KEY) -> AnswerKey:
    return AnswerKey.model_validate_json(Path(path).read_text(encoding="utf-8"))


def answer_key_sha256(path: Path = DEFAULT_ANSWER_KEY) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_against_catalogue(key: AnswerKey, catalogue) -> list[str]:
    """Differences between the scenario and the workbook (empty = consistent)."""
    problems = []
    for p in key.scenario:
        c = catalogue.products.get(p.product_id)
        if c is None:
            problems.append(f"{p.product_id}: not in the catalogue")
            continue
        for field, want, have in (("catalogue_name", p.catalogue_name, c.product_name), ("brand", p.brand, c.brand),
                                  ("model", p.model, c.manufacturer_model), ("category", p.category, c.category),
                                  ("catalogue_url", p.catalogue_url, str(c.product_url)),
                                  ("specifications", p.specifications, c.specifications)):  # fmt: skip
            if want != have:
                problems.append(f"{p.product_id}: {field} {want!r} != workbook {have!r}")
    for r in key.planned_runs:
        if len(r.product_ids) > 5:
            problems.append(f"planned run {r.name}: more than 5 products (PIPELINE_MAX_PRODUCTS_PER_RUN default)")
    return problems


# --------------------------------------------------------------------------- term matching

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalise(text: str) -> str:
    return " " + _NON_ALNUM.sub(" ", (text or "").lower()).strip() + " "


def has_term(text: str, term: str) -> bool:
    t = normalise(term).strip()
    return bool(t) and f" {t} " in normalise(text)


def group_hit(text: str, group: TermGroup) -> str | None:
    """The first alternative of `group` found in `text`, or None."""
    return next((t for t in group.any_of if has_term(text, t)), None)


def to_json(key: AnswerKey) -> str:
    return json.dumps(key.model_dump(mode="json"), indent=1, ensure_ascii=False) + "\n"
