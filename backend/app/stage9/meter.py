"""Per-run HTTP meter: the hard spending cap of a budgeted Stage 9 run.

Attached to every paid client created inside the run (Exa search/retrieval, OpenRouter), it
sees each HTTP attempt, including transport retries and the LLM repair attempt:

  before sending: work out the attempt's worst-case cost (`budget` module docstring). The
      attempt is refused (BudgetExceededError, nothing sent) if that cost cannot be bounded
      (unknown host or endpoint, an oversized LLM request, too many search results, a missing
      max_completion_tokens), or if charges so far + every attempt still in flight + this one
      would exceed the run's reservation;
  after the response: charge the provider-reported cost (Exa costDollars.total, OpenRouter
      usage.cost) when the response is 2xx and reports one; otherwise charge the full bound;
  at the end: any attempt that never got a response (timeout, connection error) is charged
      its full bound.

So a run can never be charged more than its reservation unless a provider bills more than the
configured maximum price, which reconciliation flags. Every attempt is written to the ledger
as it settles, so a crash loses nothing that was already known.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

import httpx

from app.services.search.http import ProviderRequestError
from app.stage9.budget import AttemptOutcome, BudgetLedger, UnitPrices

BUDGET_STATUS = 402  # account-level: the intelligence agent stops sending further LLM 2 calls


class BudgetExceededError(ProviderRequestError):
    """Raised by the meter before an attempt is sent. A ProviderError, so agents record it as a
    failed call (and the run continues with what it has) instead of crashing."""

    def __init__(self, provider: str, message: str):
        super().__init__(provider, f"Stage 9 budget: {message}", BUDGET_STATUS)


EXA_HOST = "api.exa.ai"
OPENROUTER_HOST = "openrouter.ai"


@dataclass
class _InFlight:
    provider: str
    endpoint: str
    bound: Decimal


def _decimal(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except ArithmeticError:
        return None
    return d if d.is_finite() and d >= 0 else None


class RunMeter:
    def __init__(self, run_id: str, reservation: Decimal, prices: UnitPrices, ledger: BudgetLedger):
        self.run_id, self.reservation, self.prices, self.ledger = run_id, reservation, prices, ledger
        self.charged = Decimal(0)
        self.inflight: dict[int, tuple[httpx.Request, _InFlight]] = {}
        self.attempts = 0
        self.refused = 0
        self.closed = False
        # Fail closed: once an attempt could not be written to the ledger, accounting is no longer
        # trustworthy, so every further paid attempt of this run is refused.
        self.accounting_error: str | None = None

    def _record(self, *args) -> None:
        try:
            self.ledger.record_attempt(self.run_id, *args)
        except Exception as e:  # sqlite errors, a locked or missing ledger, ...
            if self.accounting_error is None:
                self.accounting_error = f"{type(e).__name__}: {e}"[:300]

    # ---------------------------------------------------------------- wiring

    def attach(self, http, provider: str) -> None:
        if provider not in ("exa", "openrouter"):
            raise BudgetExceededError(provider, f"no Stage 9 prices for provider {provider!r}; only exa and "
                                                "openrouter can be used")  # fmt: skip
        http.add_event_hooks(request=self.on_request, response=self.on_response)

    # ---------------------------------------------------------------- bounds

    def bound(self, request: httpx.Request) -> tuple[str, str, Decimal]:
        """(provider, endpoint, worst-case USD) for one attempt. Raises BudgetExceededError if unbounded."""
        host, path = (request.url.host or "").lower(), request.url.path
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = None
        if host == EXA_HOST:
            if not isinstance(body, dict):
                raise BudgetExceededError("exa", "request body is not JSON: cost cannot be bounded")
            if path == "/search":
                n = body.get("numResults")
                if not isinstance(n, int) or n < 1 or n > self.prices.exa_search_max_results:
                    raise BudgetExceededError("exa", f"search asks for {n!r} results; the configured maximum price "
                                                     f"covers at most {self.prices.exa_search_max_results}")  # fmt: skip
                return "exa", "/search", self.prices.exa_search
            if path == "/contents":
                urls = body.get("urls")
                if not isinstance(urls, list) or not urls:
                    raise BudgetExceededError("exa", "contents request without URLs: cost cannot be bounded")
                return "exa", "/contents", self.prices.exa_page * len(urls)
            raise BudgetExceededError("exa", f"endpoint {path} has no configured price")
        if host == OPENROUTER_HOST:
            if not path.endswith("/chat/completions"):
                raise BudgetExceededError("openrouter", f"endpoint {path} has no configured price")
            size = len(request.content or b"")
            if size > self.prices.llm_max_request_bytes:
                raise BudgetExceededError("openrouter", f"request of {size} bytes exceeds STAGE9_LLM_MAX_REQUEST_BYTES "
                                                        f"({self.prices.llm_max_request_bytes})")  # fmt: skip
            out = body.get("max_completion_tokens") if isinstance(body, dict) else None
            if not isinstance(out, int) or out < 1:
                raise BudgetExceededError("openrouter", "request without max_completion_tokens: output cost "
                                                        "cannot be bounded")  # fmt: skip
            return "openrouter", "/chat/completions", self.prices.llm_call_max(size, out)
        raise BudgetExceededError(host or "unknown", f"host {host!r} has no configured price")

    @property
    def committed(self) -> Decimal:
        return self.charged + sum((f.bound for _, f in self.inflight.values()), Decimal(0))

    # ---------------------------------------------------------------- hooks

    async def on_request(self, request: httpx.Request) -> None:
        provider = (request.url.host or "").lower()
        try:
            if self.closed:
                raise BudgetExceededError(provider, "the run's budget meter is closed")
            if self.accounting_error:
                raise BudgetExceededError(provider, f"ledger accounting failed ({self.accounting_error}); no further "
                                                    "paid calls in this run")  # fmt: skip
            provider, endpoint, bound = self.bound(request)
            if self.committed + bound > self.reservation:
                raise BudgetExceededError(provider, f"this call could cost up to {bound} USD and only "
                                                    f"{self.reservation - self.committed} USD of the run's "
                                                    f"{self.reservation} USD reservation is left")  # fmt: skip
        except BudgetExceededError as e:
            self.refused += 1
            self._record("exa" if provider in ("exa", EXA_HOST) else "openrouter", request.url.path, Decimal(0), None,
                         Decimal(0), None, AttemptOutcome.REFUSED)  # fmt: skip
            raise e from None
        self.attempts += 1
        self.inflight[id(request)] = (request, _InFlight(provider, endpoint, bound))

    async def on_response(self, response: httpx.Response) -> None:
        entry = self.inflight.pop(id(response.request), None)
        if entry is None:
            return
        _, f = entry
        reported = None
        if 200 <= response.status_code < 300:
            try:
                await response.aread()
                data = response.json()
            except Exception:  # unreadable body: cost unknown, charged at the bound
                data = None
            if isinstance(data, dict):
                if f.provider == "exa":
                    reported = _decimal((data.get("costDollars") or {}).get("total"))
                else:
                    reported = _decimal((data.get("usage") or {}).get("cost"))
        if reported is not None:
            charged, outcome = reported, AttemptOutcome.REPORTED
        else:
            charged, outcome = f.bound, AttemptOutcome.UNREPORTED
        self.charged += charged
        self._record(f.provider, f.endpoint, f.bound, reported, charged, response.status_code, outcome)

    def close(self) -> None:
        """End of run: attempts that never got a response are charged at their bound."""
        for _, f in list(self.inflight.values()):
            self.charged += f.bound
            self._record(f.provider, f.endpoint, f.bound, None, f.bound, None, AttemptOutcome.NO_RESPONSE)
        self.inflight.clear()
        self.closed = True
