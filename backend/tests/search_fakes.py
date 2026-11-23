"""Helpers for provider tests: a recording httpx MockTransport and fake sleep."""

import json
import tempfile
from pathlib import Path

import httpx

from app.core.config import Settings
from app.services.search.http import ProviderHttpClient

FAKE_KEY = "test-key-should-never-appear-in-errors"


class Recorder:
    """Returns queued responses in order and records each request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def body(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content)


class FakeSleep:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def ok(payload: dict, status: int = 200, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


def http_client(provider: str, recorder: Recorder, sleep=None, max_retries: int = 2) -> ProviderHttpClient:
    return ProviderHttpClient(
        provider,
        httpx.AsyncClient(transport=httpx.MockTransport(recorder)),
        max_retries=max_retries,
        sleep=sleep or FakeSleep(),
    )


def make_settings(**overrides) -> Settings:
    values = {"exa_api_key": None, "tavily_api_key": None, "brave_search_api_key": None,
              "openrouter_api_key": None, "llm1_model": None,
              "search_provider": None,
              # Never touch the real review database from tests.
              "review_db_path": Path(tempfile.mkdtemp(prefix="review-test-")) / "review.sqlite3",
              # No network from tests: image lookup is off unless a test injects a fake finder,
              # and the image cache and catalogue sidecar never point at the real data folder.
              "product_images_enabled": False,
              "image_cache_dir": Path(tempfile.mkdtemp(prefix="image-cache-test-")),
              "catalogue_images_path": Path(tempfile.mkdtemp(prefix="catimg-test-")) / "product_images.json",
              # Stage 9 budget mode is off unless a test turns it on (a stray STAGE9_* env var cannot).
              "stage9_ledger_path": None}  # fmt: skip
    values.update(overrides)
    return Settings(_env_file=None, **values)
