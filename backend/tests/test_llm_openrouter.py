import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel, Field

from app.services.llm import LLMNotConfiguredError, LLMOutputError, OpenRouterClient, build_llm1, strict_json_schema
from app.services.search.http import ProviderAuthError, ProviderNotConfiguredError
from tests.search_fakes import FAKE_KEY, Recorder, http_client, make_settings, ok


class Inner(BaseModel):
    title: str  # a field literally named "title" must survive schema cleaning
    score: float = Field(ge=0, le=1)


class Out(BaseModel):
    name: str | None
    items: list[Inner]


def completion(content: str, *, finish="stop", cost=0.0012, pt=100, ct=20, rid="gen-1", model="test/model"):
    return ok({"id": rid, "model": model,
               "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
               "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct, "cost": cost}})  # fmt: skip


GOOD = json.dumps({"name": "x", "items": [{"title": "a", "score": 0.5}]})


def client(rec, **kw):
    return OpenRouterClient(http_client("openrouter", rec), FAKE_KEY, "test/model", **kw)


def run(coro):
    return asyncio.run(coro)


def test_strict_schema_shape():
    s = strict_json_schema(Out)
    assert s["additionalProperties"] is False and s["required"] == ["name", "items"]
    inner = s["$defs"]["Inner"]
    assert inner["required"] == ["title", "score"] and "title" in inner["properties"]
    assert "minimum" not in json.dumps(s) and "maximum" not in json.dumps(s)
    assert {"type": "null"} in s["properties"]["name"]["anyOf"]


def test_request_shape_and_result():
    rec = Recorder(completion(GOOD))
    res = run(client(rec, temperature=0.1, max_completion_tokens=500).complete_structured(
        system="sys", user="usr", output_model=Out, schema_name="out"))  # fmt: skip
    req = rec.requests[0]
    assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    body = rec.body()
    assert body["model"] == "test/model" and body["temperature"] == 0.1 and body["max_completion_tokens"] == 500
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["name"] == "out"
    assert body["provider"] == {"require_parameters": True}
    assert res.value == Out.model_validate_json(GOOD)
    assert (res.model, res.request_ids, res.attempts) == ("test/model", ["gen-1"], 1)
    assert res.usage.prompt_tokens == 100 and res.usage.cost_usd == Decimal("0.0012")


def test_repairs_invalid_output_once_and_sums_usage():
    rec = Recorder(completion("not json", rid="a"), completion(GOOD, rid="b"))
    res = run(client(rec).complete_structured(system="s", user="u", output_model=Out, schema_name="out"))
    assert res.attempts == 2 and res.request_ids == ["a", "b"]
    assert res.usage.prompt_tokens == 200 and res.usage.cost_usd == Decimal("0.0024")
    repair_msgs = rec.body(1)["messages"]
    assert repair_msgs[2] == {"role": "assistant", "content": "not json"}
    assert "did not match the required JSON schema" in repair_msgs[3]["content"]


def test_gives_up_after_repair():
    bad = json.dumps({"name": "x", "items": [{"title": "a", "score": 7}]})  # score > 1
    rec = Recorder(completion(bad), completion(bad))
    with pytest.raises(LLMOutputError, match="after repair"):
        run(client(rec).complete_structured(system="s", user="u", output_model=Out, schema_name="out"))
    assert len(rec.requests) == 2


def test_truncated_and_error_payloads():
    with pytest.raises(LLMOutputError, match="truncated"):
        run(client(Recorder(completion(GOOD[:10], finish="length"))).complete_structured(
            system="s", user="u", output_model=Out, schema_name="out"))  # fmt: skip
    with pytest.raises(LLMOutputError, match="Provider error"):
        run(client(Recorder(ok({"error": {"message": "no endpoints support json_schema"}}))).complete_structured(
            system="s", user="u", output_model=Out, schema_name="out"))  # fmt: skip


def test_auth_error_does_not_leak_key():
    rec = Recorder(ok({"error": {"message": "bad key"}}, 401))
    with pytest.raises(ProviderAuthError) as exc:
        run(client(rec).complete_structured(system="s", user="u", output_model=Out, schema_name="out"))
    assert FAKE_KEY not in str(exc.value)


def test_build_llm1_requires_key_and_model():
    with pytest.raises(LLMNotConfiguredError, match="OPENROUTER_API_KEY"):
        build_llm1(make_settings(openrouter_api_key=None, llm1_model="m"))
    with pytest.raises(LLMNotConfiguredError, match="LLM1_MODEL"):
        build_llm1(make_settings(openrouter_api_key=FAKE_KEY, llm1_model=None))
    assert issubclass(LLMNotConfiguredError, ProviderNotConfiguredError)
    llm = build_llm1(make_settings(openrouter_api_key=FAKE_KEY, llm1_model="vendor/model"),
                     httpx.AsyncClient(transport=httpx.MockTransport(Recorder())))  # fmt: skip
    assert llm.model == "vendor/model"
    run(llm.aclose())
    assert FAKE_KEY not in repr(make_settings(openrouter_api_key=FAKE_KEY))
