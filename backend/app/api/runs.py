"""Pipeline runs API (Phase 7).

    POST /api/runs                     start a run (202) -> ExecutionRun
    GET  /api/runs                     recent runs (temporary, in memory)
    GET  /api/runs/{run_id}            run state: status, stages, progress, summary
    GET  /api/runs/{run_id}/result     consolidated results (filled progressively)
    GET  /api/runs/{run_id}/events     events as JSON, ?after=<seq> (polling)
    GET  /api/runs/{run_id}/stream     events as Server-Sent Events; resumes from
                                       Last-Event-ID or ?after=<seq>; ends after the terminal event
    POST /api/runs/{run_id}/cancel     request cancellation (202)

Runs are temporary: kept in memory only, pruned after PIPELINE_RUN_TTL_MINUTES
or beyond PIPELINE_MAX_RUNS_RETAINED, and lost on restart.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.orchestration.pipeline import RunManager, RunRequestError
from app.orchestration.schemas import TERMINAL, ExecutionEvent, ExecutionRun, PipelineResult, RunRequest

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _manager(request: Request) -> RunManager:
    return request.app.state.run_manager


def _run(request: Request, run_id: str) -> ExecutionRun:
    run = _manager(request).store.get(run_id)
    if run is None:
        raise HTTPException(404, detail=f"Run {run_id} not found (runs are temporary and may have expired)")
    return run


@router.post("", response_model=ExecutionRun, status_code=status.HTTP_202_ACCEPTED)
async def start_run(body: RunRequest, request: Request) -> ExecutionRun:
    try:
        return _manager(request).start(body)
    except RunRequestError as e:
        raise HTTPException(e.status_code, detail=e.detail) from None


@router.get("", response_model=list[ExecutionRun])
def list_runs(request: Request) -> list[ExecutionRun]:
    return _manager(request).store.list_runs()


@router.get("/{run_id}", response_model=ExecutionRun)
def get_run(run_id: str, request: Request) -> ExecutionRun:
    return _run(request, run_id)


@router.get("/{run_id}/result", response_model=PipelineResult)
def get_result(run_id: str, request: Request) -> PipelineResult:
    _run(request, run_id)
    return _manager(request).store.get_result(run_id)


@router.get("/{run_id}/events", response_model=list[ExecutionEvent])
def get_events(run_id: str, request: Request, after: Annotated[int, Query(ge=0)] = 0) -> list[ExecutionEvent]:
    _run(request, run_id)
    return _manager(request).store.events_after(run_id, after)


@router.get("/{run_id}/stream")
async def stream_events(
    run_id: str, request: Request, after: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    _run(request, run_id)
    store = _manager(request).store
    heartbeat = request.app.state.settings.pipeline_sse_heartbeat_seconds
    start = after if after is not None else int(last_event_id) if (last_event_id or "").isdigit() else 0

    async def events():
        seq = start
        yield "retry: 3000\n\n"
        while True:
            batch = await store.wait_for_events(run_id, seq, heartbeat)
            for e in batch:
                yield f"id: {e.seq}\nevent: {e.type}\ndata: {e.model_dump_json()}\n\n"
                seq = e.seq
            run = store.get(run_id)
            if run is None:  # pruned
                return
            if run.status in TERMINAL and not store.events_after(run_id, seq):
                return
            if not batch:
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})  # fmt: skip


@router.post("/{run_id}/cancel", response_model=ExecutionRun, status_code=status.HTTP_202_ACCEPTED)
def cancel_run(run_id: str, request: Request) -> ExecutionRun:
    try:
        return _manager(request).cancel(run_id)
    except RunRequestError as e:
        raise HTTPException(e.status_code, detail=e.detail) from None
