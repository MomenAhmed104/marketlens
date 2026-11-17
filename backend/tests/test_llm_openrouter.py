import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel, Field, ValidationError

from app.agents.discovery import llm1_disabled_reason
from app.core.config import Settings
from app.services.llm import (
    LLMNotConfiguredError,
    LLMOutputError,
    OpenRouterClient,
    build_llm1,
    build_llm2,
    strict_json_schema,
)
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


# --- Optional temperature / reasoning effort (reasoning models such as openai/gpt-6-luna) ---


def test_temperature_and_reasoning_are_omitted_by_default():
    """With require_parameters, an unsupported parameter makes OpenRouter refuse to route."""
    rec = Recorder(completion(GOOD))
    run(client(rec).complete_structured(system="s", user="u", output_model=Out, schema_name="out"))
    body = rec.body()
    assert "temperature" not in body and "reasoning" not in body
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True}


def test_explicit_zero_temperature_is_sent():
    rec = Recorder(completion(GOOD))
    run(client(rec, temperature=0.0).complete_structured(system="s", user="u", output_model=Out, schema_name="out"))
    assert rec.body()["temperature"] == 0.0


def test_reasoning_effort_is_sent_and_reasoning_text_excluded():
    rec = Recorder(completion(GOOD))
    run(client(rec, reasoning_effort="low").complete_structured(
        system="s", user="u", output_model=Out, schema_name="out"))  # fmt: skip
    assert rec.body()["reasoning"] == {"effort": "low", "exclude": True}


def reasoning_completion(content, *, finish="stop", ct=8000, reasoning=8000, cost=0.004, rid="gen-r"):
    return ok({"id": rid, "model": "openai/gpt-6-luna",
               "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
               "usage": {"prompt_tokens": 3000, "completion_tokens": ct, "cost": cost,
                         "completion_tokens_details": {"reasoning_tokens": reasoning}}})  # fmt: skip


@pytest.mark.parametrize("content", [None, ""])
def test_reasoning_exhausting_the_budget_fails_without_repair_and_keeps_usage(content):
    rec = Recorder(reasoning_completion(content, finish="length"), completion(GOOD))
    with pytest.raises(LLMOutputError, match="budget used up before any answer") as exc:
        run(client(rec, max_completion_tokens=8000, reasoning_effort="low").complete_structured(
            system="s", user="u", output_model=Out, schema_name="out"))  # fmt: skip
    assert len(rec.requests) == 1  # no repair retry: it would hit the same budget
    assert "8000 completion tokens (8000 reasoning) of 8000" in str(exc.value)
    assert "MAX_COMPLETION_TOKENS" in str(exc.value) and "REASONING_EFFORT" in str(exc.value)
    assert exc.value.usage.cost_usd == Decimal("0.004") and exc.value.usage.reasoning_tokens == 8000
    assert exc.value.request_ids == ["gen-r"]


def test_truncated_partial_json_is_never_parsed():
    """A cut-off reply can still be valid JSON; it must not be accepted."""
    rec = Recorder(reasoning_completion(GOOD, finish="length", ct=500, reasoning=450))
    with pytest.raises(LLMOutputError, match="Output truncated") as exc:
        run(client(rec).complete_structured(system="s", user="u", output_model=Out, schema_name="out"))
    assert len(rec.requests) == 1 and exc.value.usage.completion_tokens == 500


def test_failed_repair_keeps_usage_of_both_calls():
    bad = json.dumps({"name": "x", "items": [{"title": "a", "score": 7}]})
    rec = Recorder(completion(bad, rid="a"), completion(bad, rid="b"))
    with pytest.raises(LLMOutputError) as exc:
        run(client(rec).complete_structured(system="s", user="u", output_model=Out, schema_name="out"))
    assert exc.value.usage.cost_usd == Decimal("0.0024") and exc.value.request_ids == ["a", "b"]


def test_reasoning_tokens_are_recorded_on_success():
    rec = Recorder(reasoning_completion(GOOD, ct=900, reasoning=700))
    res = run(client(rec).complete_structured(system="s", user="u", output_model=Out, schema_name="out"))
    assert res.usage.completion_tokens == 900 and res.usage.reasoning_tokens == 700


# --- Settings ---


def test_builders_pass_independent_per_llm_settings():
    s = make_settings(openrouter_api_key=FAKE_KEY, llm1_model="openai/gpt-6-luna", llm2_model="other/model",
                      llm1_reasoning_effort="low", llm2_temperature=0.0,
                      llm1_max_completion_tokens=12000, llm2_max_completion_tokens=8000)  # fmt: skip
    t = httpx.AsyncClient(transport=httpx.MockTransport(Recorder()))
    l1, l2 = build_llm1(s, t), build_llm2(s, t)
    assert (l1.model, l1.temperature, l1.reasoning_effort, l1.max_completion_tokens) == (
        "openai/gpt-6-luna", None, "low", 12000)
    assert (l2.model, l2.temperature, l2.reasoning_effort, l2.max_completion_tokens) == (
        "other/model", 0.0, None, 8000)
    run(t.aclose())


def test_blank_env_values_mean_unset(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM1_MODEL=   \nLLM2_MODEL= openai/gpt-6-luna \nLLM1_TEMPERATURE=\n"
                   "LLM2_TEMPERATURE=0\nLLM1_REASONING_EFFORT=\nLLM2_REASONING_EFFORT=low\n", encoding="utf-8")  # fmt: skip
    s = Settings(_env_file=env)
    assert s.llm1_model is None and s.llm2_model == "openai/gpt-6-luna"
    assert s.llm1_temperature is None and s.llm2_temperature == 0.0
    assert s.llm1_reasoning_effort is None and s.llm2_reasoning_effort == "low"


def test_whitespace_only_llm1_model_is_not_configured():
    with pytest.raises(LLMNotConfiguredError, match="LLM1_MODEL"):
        build_llm1(make_settings(openrouter_api_key=FAKE_KEY, llm1_model="   "))
    assert llm1_disabled_reason(make_settings(openrouter_api_key=FAKE_KEY, llm1_model="  ")) == "LLM1_MODEL not set"


def test_invalid_reasoning_effort_is_rejected():
    with pytest.raises(ValidationError):
        make_settings(llm1_reasoning_effort="extreme")
