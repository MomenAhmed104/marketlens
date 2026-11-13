"""Temporary run state (Phase 7).

`RunStore` is the interface; `InMemoryRunStore` the only implementation, as
decided for the MVP: nothing is written to disk, a backend restart clears every
run, and finished runs are pruned by age and count. A persistent store can be
added later behind the same interface.

All methods run on the application's event loop (no threads).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.orchestration.schemas import TERMINAL, ExecutionEvent, ExecutionRun, PipelineResult


class RunStore(ABC):
    @abstractmethod
    def create(self, run: ExecutionRun, result: PipelineResult) -> None: ...

    @abstractmethod
    def get(self, run_id: str) -> ExecutionRun | None: ...

    @abstractmethod
    def get_result(self, run_id: str) -> PipelineResult | None: ...

    @abstractmethod
    def list_runs(self) -> list[ExecutionRun]: ...

    @abstractmethod
    def save(self, run: ExecutionRun, result: PipelineResult) -> None:
        """Called after every state change (a persistent store would write here)."""

    @abstractmethod
    def append_event(self, event: ExecutionEvent) -> None: ...

    @abstractmethod
    def events_after(self, run_id: str, after_seq: int) -> list[ExecutionEvent]: ...

    @abstractmethod
    async def wait_for_events(self, run_id: str, after_seq: int, timeout: float) -> list[ExecutionEvent]:
        """Events with seq > after_seq; waits up to `timeout` seconds for new ones."""

    @abstractmethod
    def prune(self, now: datetime) -> list[str]: ...


@dataclass
class _Record:
    run: ExecutionRun
    result: PipelineResult
    events: list[ExecutionEvent] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class InMemoryRunStore(RunStore):
    def __init__(self, max_retained: int, ttl_minutes: int):
        self.max_retained = max_retained
        self.ttl = timedelta(minutes=ttl_minutes)
        self._runs: dict[str, _Record] = {}

    def create(self, run: ExecutionRun, result: PipelineResult) -> None:
        if run.run_id in self._runs:
            raise ValueError(f"Run {run.run_id} already exists")
        self._runs[run.run_id] = _Record(run, result)

    def get(self, run_id: str) -> ExecutionRun | None:
        rec = self._runs.get(run_id)
        return rec.run if rec else None

    def get_result(self, run_id: str) -> PipelineResult | None:
        rec = self._runs.get(run_id)
        return rec.result if rec else None

    def list_runs(self) -> list[ExecutionRun]:
        return sorted((r.run for r in self._runs.values()), key=lambda r: r.created_at, reverse=True)

    def save(self, run: ExecutionRun, result: PipelineResult) -> None:
        rec = self._runs.get(run.run_id)
        if rec:
            rec.run, rec.result = run, result
            self._notify(rec)

    def append_event(self, event: ExecutionEvent) -> None:
        rec = self._runs.get(event.run_id)
        if rec is None:
            return
        rec.events.append(event)
        rec.run.last_event_seq = event.seq
        self._notify(rec)

    def events_after(self, run_id: str, after_seq: int) -> list[ExecutionEvent]:
        rec = self._runs.get(run_id)
        return [e for e in rec.events if e.seq > after_seq] if rec else []

    async def wait_for_events(self, run_id: str, after_seq: int, timeout: float) -> list[ExecutionEvent]:
        rec = self._runs.get(run_id)
        if rec is None:
            return []
        if not (new := self.events_after(run_id, after_seq)) and rec.run.status not in TERMINAL:
            changed = rec.changed  # grabbed without awaiting: no lost wake-ups on a single loop
            try:
                await asyncio.wait_for(changed.wait(), timeout)
            except TimeoutError:
                pass
            new = self.events_after(run_id, after_seq)
        return new

    def prune(self, now: datetime) -> list[str]:
        """Drop finished runs older than the TTL, then the oldest finished beyond the count limit."""
        finished = sorted((r.run for r in self._runs.values() if r.run.status in TERMINAL),
                          key=lambda r: r.finished_at or r.created_at)  # fmt: skip
        drop = [r.run_id for r in finished if now - (r.finished_at or r.created_at) > self.ttl]
        keep = [r for r in finished if r.run_id not in drop]
        drop += [r.run_id for r in keep[: max(0, len(self._runs) - len(drop) - self.max_retained)]]
        for run_id in drop:
            self._runs.pop(run_id, None)
        return drop

    @staticmethod
    def _notify(rec: _Record) -> None:
        rec.changed.set()
        rec.changed = asyncio.Event()
