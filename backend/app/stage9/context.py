"""Which budget meter (if any) governs paid clients created in the current task.

`active_meter` is called by the only two places that create paid HTTP clients
(`services.search.build_provider` and `services.llm._build`), before the client is created:

  - Stage 9 budget mode off (no STAGE9_LEDGER_PATH): returns None (normal behaviour);
  - budget mode on and a budgeted run is active in this task: returns its meter, and the
    builder attaches it to the new client, so every HTTP attempt is metered;
  - budget mode on and no budgeted run is active (a standalone endpoint, a CLI, a benchmark):
    raises, so nothing can spend outside the ledger.

The meter is carried in a ContextVar, which asyncio copies into the run's task when it is
created, so concurrent runs each see their own meter.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

CURRENT_METER: ContextVar[Any] = ContextVar("stage9_current_meter", default=None)


def budget_mode(settings) -> bool:
    return getattr(settings, "stage9_ledger_path", None) is not None


def active_meter(settings, provider: str):
    """The active run's meter, None outside budget mode; raises in budget mode without a run."""
    if not budget_mode(settings):
        return None
    meter = CURRENT_METER.get()
    if meter is None:
        from app.services.search.http import ProviderNotConfiguredError

        raise ProviderNotConfiguredError(
            provider, "Stage 9 budget mode is on: paid providers can only be used by a budgeted pipeline run "
                      "(POST /api/runs)")  # fmt: skip
    return meter
