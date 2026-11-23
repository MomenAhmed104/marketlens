"""Stage 9 evaluation harness: one scenario run through the real dashboard path.

The harness builds the real FastAPI app (`app.main.create_app`) with evaluation settings and
drives it over HTTP exactly like the dashboard: `POST /api/runs`, then `GET /api/runs/{id}` until
the run ends, then `/result` and `/events`. Nothing is re-implemented: the orchestrator, the
agents, matching and the Stage 9 budgeted run manager are the production code.

Isolation and safety:
  - the app runs in this process (Starlette TestClient: no port, no running server is touched);
  - REVIEW_DB_PATH points at an isolated evaluation database (`isolated_db`), never data/review/;
  - budget mode is REQUIRED: the harness refuses to start without an existing Stage 9 ledger,
    so every paid call is reserved and metered (with prices unset, every run is refused);
  - product images are off by default (they never affect scores and add retailer requests).

Each run writes a new artifact folder (never overwritten): manifest.json (request, settings
summary without secrets, git HEAD, answer-key hash, paths, timings), run.json, result.json,
events.json and ledger.json (the run's reservation, charges and every metered attempt).
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.orchestration.schemas import TERMINAL, RunStatus
from app.stage9.budget import BudgetLedger, LedgerError
from app.stage9.evaluation import DEFAULT_ANSWER_KEY, answer_key_sha256
from app.stage9.isolated_db import Approval, apply_decisions, check_destination

HARNESS_VERSION = 1
# Settings copied into the manifest (never keys or secrets).
_SETTINGS_SNAPSHOT = (
    "search_provider", "llm1_model", "llm2_model", "llm1_reasoning_effort", "llm2_reasoning_effort",
    "llm1_max_completion_tokens", "llm2_max_completion_tokens", "discovery_use_llm", "intelligence_use_llm",
    "intelligence_match_proposals", "pipeline_search_auto_approved", "max_auto_search_targets",
    "pipeline_max_products_per_run", "matching_price_max_age_hours", "product_images_enabled",
    "stage9_max_reservation_per_run_usd", "stage9_exa_search_max_results", "stage9_llm_max_request_bytes",
)  # fmt: skip


class HarnessError(Exception):
    pass


@dataclass
class ScenarioRun:
    label: str
    product_ids: list[str]
    review_db: Path  # isolated evaluation database (must already exist)
    ledger: Path  # existing Stage 9 ledger
    output_dir: Path
    run_discovery: bool = True
    domains: list[str] | None = None  # optional subset of searchable domains (RunRequest.domains)
    approvals: list[Approval] = field(default_factory=list)  # applied to review_db before the run
    images: bool = False
    timeout_s: float = 1800
    poll_s: float = 1.0
    answer_key: Path = DEFAULT_ANSWER_KEY


@dataclass
class HarnessResult:
    run_id: str | None
    status: str
    artifact_dir: Path
    duration_s: float
    http_status: int
    detail: Any = None


def _git_head() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10,
                             cwd=Path(__file__).resolve().parent)  # fmt: skip
        return out.stdout.strip() or None
    except Exception:
        return None


def build_settings(cfg: ScenarioRun, **overrides):
    """Evaluation settings: the normal environment (keys, models) plus the isolated paths."""
    from app.core.config import Settings

    values: dict[str, Any] = {"review_db_path": cfg.review_db, "stage9_ledger_path": cfg.ledger,
                              "product_images_enabled": cfg.images}  # fmt: skip
    values.update(overrides)
    return Settings(**values)


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False) + "\n", encoding="utf-8")


def run_scenario(cfg: ScenarioRun, *, settings=None, prepare_app: Callable[[Any], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> HarnessResult:  # fmt: skip
    """Run one scenario through POST /api/runs and save its artifacts.

    `settings` defaults to `build_settings(cfg)`. `prepare_app(app)` runs after startup and before
    the request (tests use it to swap in fake providers; live runs pass nothing)."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    check_destination(cfg.review_db)
    if not Path(cfg.review_db).is_file():
        raise HarnessError(f"Evaluation database {cfg.review_db} does not exist; create it with isolated_db")
    try:
        BudgetLedger(cfg.ledger)  # must already exist: the harness never creates a ledger
    except LedgerError as e:
        raise HarnessError(str(e)) from None
    s = settings or build_settings(cfg)
    if Path(s.review_db_path).resolve() != Path(cfg.review_db).resolve():
        raise HarnessError("settings.review_db_path is not the evaluation database")
    if s.stage9_ledger_path is None or Path(s.stage9_ledger_path).resolve() != Path(cfg.ledger).resolve():
        raise HarnessError("Budget mode must be on, with the scenario's ledger")
    if cfg.approvals:
        apply_decisions(cfg.review_db, cfg.approvals, s.discovery_excluded_competitor_domains)

    started_at = datetime.now(UTC)
    request = {"product_ids": cfg.product_ids, "run_discovery": cfg.run_discovery}
    if cfg.domains:
        request["domains"] = cfg.domains
    app = create_app(s)
    t0 = time.monotonic()
    run_id, status, detail, http_status = None, "NOT_STARTED", None, 0
    run = result = None
    events: list = []
    with TestClient(app) as client:
        if prepare_app is not None:
            prepare_app(app)
        r = client.post("/api/runs", json=request)
        http_status = r.status_code
        if r.status_code != 202:
            detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
            status = "REFUSED"
        else:
            run_id = r.json()["run_id"]
            while True:
                run = client.get(f"/api/runs/{run_id}").json()
                if RunStatus(run["status"]) in TERMINAL:
                    break
                if time.monotonic() - t0 > cfg.timeout_s:
                    client.post(f"/api/runs/{run_id}/cancel")
                    detail = f"harness timeout after {cfg.timeout_s:g} s: cancellation requested"
                    for _ in range(60):
                        run = client.get(f"/api/runs/{run_id}").json()
                        if RunStatus(run["status"]) in TERMINAL:
                            break
                        sleep(cfg.poll_s)
                    break
                sleep(cfg.poll_s)
            result = client.get(f"/api/runs/{run_id}/result").json()
            events = client.get(f"/api/runs/{run_id}/events").json()
            status = run["status"]
            # The ledger is reconciled when the run's task ends, just after its terminal status.
            for _ in range(100):
                entry = BudgetLedger(cfg.ledger).run(run_id)
                if entry is None or entry["status"] != "RESERVED":
                    break
                sleep(min(cfg.poll_s, 0.1))
    # The app has shut down: the run's task is finished and its ledger entry reconciled.
    duration = time.monotonic() - t0
    finished_at = datetime.now(UTC)
    ledger = BudgetLedger(cfg.ledger)
    ledger_entry = {"run": ledger.run(run_id), "attempts": ledger.attempts(run_id)} if run_id else None

    out = Path(cfg.output_dir) / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{cfg.label}-{run_id or 'refused'}"
    out.mkdir(parents=True, exist_ok=False)
    manifest = {
        "harness_version": HARNESS_VERSION, "label": cfg.label, "git_head": _git_head(), "request": request,
        "approvals": [a.model_dump(mode="json") for a in cfg.approvals], "run_id": run_id, "status": status,
        "http_status": http_status, "detail": detail, "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(), "duration_s": round(duration, 3),
        "review_db": str(Path(cfg.review_db).resolve()), "ledger": str(Path(cfg.ledger).resolve()),
        "answer_key": str(cfg.answer_key), "answer_key_sha256": answer_key_sha256(cfg.answer_key),
        "settings": {k: getattr(s, k, None) for k in _SETTINGS_SNAPSHOT},
    }  # fmt: skip
    _write(out / "manifest.json", manifest)
    _write(out / "run.json", run)
    _write(out / "result.json", result)
    _write(out / "events.json", events)
    _write(out / "ledger.json", ledger_entry)
    return HarnessResult(run_id, status, out, duration, http_status, detail)


def load_artifacts(folder: Path) -> dict[str, Any]:
    folder = Path(folder)
    return {name: json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("manifest", "run", "result", "events", "ledger")}  # fmt: skip
