"""Phase 7 runs API: start, poll, result, events, SSE stream, cancel (mocked providers, no keys)."""

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.pipeline_fakes import SlowIntelligenceAgent, harness
from tests.search_fakes import make_settings


@pytest.fixture
def api(catalogue, tmp_path):
    def make(**kw):
        h = harness(catalogue, tmp_path, **kw)
        app = create_app(make_settings(review_db_path=tmp_path / "app-review.sqlite3"))
        return app, h

    return make


def wait_terminal(client, run_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("COMPLETED", "PARTIALLY_COMPLETED", "FAILED", "CANCELLED"):
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def parse_sse(lines):
    events, cur = [], {}
    for line in lines:
        if line.startswith("id: "):
            cur["id"] = int(line[4:])
        elif line.startswith("event: "):
            cur["event"] = line[7:]
        elif line.startswith("data: "):
            cur["data"] = json.loads(line[6:])
        elif line == "" and cur:
            events.append(cur)
            cur = {}
    return events


def test_start_poll_result_events_and_stream(api):
    app, h = api()
    with TestClient(app) as client:
        app.state.run_manager = h.manager
        r = client.post("/api/runs", json={"product_ids": ["SB-001", "SB-028"]})
        assert r.status_code == 202
        run = r.json()
        assert run["status"] in ("PENDING", "RUNNING") and len(run["stages"]) == 7
        run_id = run["run_id"]

        # SSE: streams every event live and closes after the terminal event.
        with client.stream("GET", f"/api/runs/{run_id}/stream") as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            events = parse_sse(list(resp.iter_lines()))
        assert events[0]["event"] == "run_started" and events[-1]["event"] == "run_completed"
        assert [e["id"] for e in events] == list(range(1, len(events) + 1))
        assert all(e["data"]["run_id"] == run_id and e["data"]["type"] == e["event"] for e in events)

        final = wait_terminal(client, run_id)
        assert final["status"] == "COMPLETED" and final["summary"]["products_completed"] == 2
        assert {s["name"]: s["status"] for s in final["stages"]}["extraction"] == "SKIPPED"

        result = client.get(f"/api/runs/{run_id}/result").json()
        assert result["intelligence_mode"] == "LLM_DISABLED" and len(result["search_targets"]) == 13
        assert result["new_pending_domains"] and all(p["matching"] for p in result["products"])

        # Polling and resume: JSON events after a sequence number; SSE resumes from Last-Event-ID.
        polled = client.get(f"/api/runs/{run_id}/events", params={"after": 3}).json()
        assert polled[0]["seq"] == 4 and len(polled) == len(events) - 3
        with client.stream("GET", f"/api/runs/{run_id}/stream", headers={"Last-Event-ID": str(len(events) - 2)}) as resp:
            tail = parse_sse(list(resp.iter_lines()))
        assert [e["id"] for e in tail] == [len(events) - 1, len(events)]

        assert client.get("/api/runs").json()[0]["run_id"] == run_id
        r = client.post(f"/api/runs/{run_id}/cancel")
        assert r.status_code == 409  # already finished


def test_cancel_through_api(api):
    app, h = api(intel_agent_cls=SlowIntelligenceAgent)
    SlowIntelligenceAgent.gate, SlowIntelligenceAgent.entered = asyncio.Event(), asyncio.Event()
    with TestClient(app) as client:
        app.state.run_manager = h.manager
        run_id = client.post("/api/runs", json={"product_ids": ["SB-001"]}).json()["run_id"]
        deadline = time.monotonic() + 10
        while not any(e["type"] == "search_started" for e in client.get(f"/api/runs/{run_id}/events").json()):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        r = client.post(f"/api/runs/{run_id}/cancel")
        assert r.status_code == 202 and r.json()["cancel_requested"] is True
        final = wait_terminal(client, run_id)
        assert final["status"] == "CANCELLED"
        events = client.get(f"/api/runs/{run_id}/events").json()
        assert events[-1]["type"] == "run_cancelled"
        result = client.get(f"/api/runs/{run_id}/result").json()
        assert result["products"][0]["status"] == "CANCELLED" and result["discovery"] is not None


def test_errors(api):
    app, h = api()
    with TestClient(app) as client:
        app.state.run_manager = h.manager
        assert client.post("/api/runs", json={"product_ids": []}).status_code == 422
        r = client.post("/api/runs", json={"product_ids": ["SB-001", "SB-999"]})
        assert r.status_code == 404 and r.json()["detail"]["missing_ids"] == ["SB-999"]
        r = client.post("/api/runs", json={"product_ids": ["SB-001"], "domains": ["pending-shop.co.uk"]})
        assert r.status_code == 422 and r.json()["detail"]["not_allowed"] == ["pending-shop.co.uk"]
        for path in ("", "/result", "/events", "/stream"):
            assert client.get(f"/api/runs/run-missing{path}").status_code == 404
        assert client.post("/api/runs/run-missing/cancel").status_code == 404


def test_default_app_requires_search_configuration_but_no_llm_keys(tmp_path):
    s = make_settings(review_db_path=tmp_path / "r.sqlite3", search_provider=None, openrouter_api_key="sk-or-secret")
    with TestClient(create_app(s)) as client:
        r = client.post("/api/runs", json={"product_ids": ["SB-001"]})
        assert r.status_code == 503 and "SEARCH_PROVIDER" in r.text and "sk-or-secret" not in r.text
        assert client.get("/api/runs").json() == []
