"""The OpenAI-compatible adapter against a mocked transport (no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.ai.providers.base import ChatMessage, ProviderError
from app.ai.providers.openai_compat import OpenAICompatProvider


def _provider(handler, brand="gemini"):
    return OpenAICompatProvider(base_url="https://example.test/v1/", api_key="k-123",
                                model="gemini-2.5-flash", brand=brand,
                                transport=httpx.MockTransport(handler))


def test_complete_sends_system_first_and_reads_usage():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={
            "model": "gemini-2.5-flash",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 1}})

    out = _provider(handler).complete(system="sys", messages=[ChatMessage("user", "hi")],
                                      max_tokens=50)
    assert out.text == "OK" and out.tokens_in == 12 and out.tokens_out == 1
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["auth"] == "Bearer k-123"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["body"]["max_tokens"] == 50 + 1536  # room for Gemini's thinking


def test_openai_uses_max_completion_tokens():
    body = {}

    def handler(req):
        body.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    _provider(handler, brand="openai").complete(system="s", messages=[], max_tokens=7)
    assert body["max_completion_tokens"] == 7 and "max_tokens" not in body


@pytest.mark.parametrize("status,code,retryable", [
    (401, "auth", False), (403, "auth", False), (404, "not_found", False),
    (429, "rate_limit", True), (503, "unavailable", True), (400, "bad_request", False),
])
def test_errors_are_classified(status, code, retryable):
    def handler(req):
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(ProviderError) as e:
        _provider(handler).complete(system="s", messages=[ChatMessage("user", "x")])
    assert e.value.code == code and e.value.retryable is retryable


def test_gemini_style_error_list_body():
    def handler(req):
        return httpx.Response(400, json=[{"error": {"message": "API key not valid"}}])

    with pytest.raises(ProviderError) as e:
        _provider(handler).list_models()
    assert "API key not valid" in e.value.message


def test_list_models_filters_non_chat_and_strips_prefix():
    def handler(req):
        return httpx.Response(200, json={"data": [
            {"id": "models/gemini-2.5-flash"}, {"id": "models/text-embedding-004"},
            {"id": "models/imagen-3.0"}, {"id": "models/gemini-2.5-pro"}]})

    assert _provider(handler).list_models() == ["gemini-2.5-flash", "gemini-2.5-pro"]


def test_timeout_is_retryable():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(ProviderError) as e:
        _provider(handler).complete(system="s", messages=[])
    assert e.value.code == "timeout" and e.value.retryable


def test_ollama_asks_a_reasoning_model_not_to_think_and_inline_thinking_is_dropped():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"model": "qwen3:8b", "choices": [{"message": {
            "content": "<think>working it out</think>\n{\"tool\":\"compare\"}"},
            "finish_reason": "stop"}]})
    p = OpenAICompatProvider(base_url="http://localhost:11434/v1", api_key=None, model="qwen3:8b",
                             brand="ollama", transport=httpx.MockTransport(handler))
    out = p.complete(system="s", messages=[], json_mode=True)
    assert seen["reasoning_effort"] == "none"
    assert out.text == '{"tool":"compare"}'


def test_other_brands_are_not_sent_a_reasoning_setting():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    _provider(handler, brand="groq").complete(system="s", messages=[])
    assert "reasoning_effort" not in seen and seen["max_tokens"] == 2048


# --- busy providers ------------------------------------------------------------------ #

def _busy_then_ok(statuses, headers=None):
    calls = []

    def handler(req):
        calls.append(1)
        s = statuses[min(len(calls) - 1, len(statuses) - 1)]
        if s == 200:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
        return httpx.Response(s, headers=headers or {},
                              json={"error": {"message": "This model is currently experiencing high demand."}})
    return handler, calls


@pytest.fixture
def no_wait(monkeypatch):
    waits = []
    import app.ai.providers.openai_compat as oc
    monkeypatch.setattr(oc, "_sleep", waits.append)
    return waits


def test_a_busy_provider_is_retried_and_the_answer_comes_through(no_wait):
    handler, calls = _busy_then_ok([503, 503, 200])
    out = _provider(handler).complete(system="s", messages=[])
    assert out.text == "ok" and len(calls) == 3 and no_wait == [1.5, 4.0]


def test_it_gives_up_after_three_tries_and_says_so(no_wait):
    handler, calls = _busy_then_ok([503])
    with pytest.raises(ProviderError) as e:
        _provider(handler).complete(system="s", messages=[])
    assert len(calls) == 3 and e.value.retryable and "tried 3 times" in e.value.message
    assert "overloaded" in e.value.message


def test_a_long_retry_after_is_a_quota_not_a_blip(no_wait):
    handler, calls = _busy_then_ok([429], headers={"retry-after": "3600"})
    with pytest.raises(ProviderError) as e:
        _provider(handler).complete(system="s", messages=[])
    assert len(calls) == 1 and no_wait == [] and e.value.code == "rate_limit"


def test_a_short_retry_after_is_honoured(no_wait):
    handler, calls = _busy_then_ok([429, 200], headers={"retry-after": "6"})
    _provider(handler).complete(system="s", messages=[])
    assert no_wait == [6.0]


def test_client_errors_are_not_retried(no_wait):
    handler, calls = _busy_then_ok([400])
    with pytest.raises(ProviderError):
        _provider(handler).complete(system="s", messages=[])
    assert len(calls) == 1


# --- thinking models (Gemini) ----------------------------------------------------------- #

def _gemini(replies):
    """replies: a list of (status, body) returned in turn; records each request."""
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        status, body = replies[min(len(sent) - 1, len(replies) - 1)]
        return httpx.Response(status, json=body)
    return _provider(handler, brand="gemini"), sent


def _ok(text, finish="stop"):
    return 200, {"choices": [{"message": {"content": text}, "finish_reason": finish}]}


def test_gemini_thinks_little_and_gets_room_for_it():
    p, sent = _gemini([_ok("fine")])
    p.complete(system="s", messages=[], max_tokens=500)
    assert sent[0]["reasoning_effort"] == "low" and sent[0]["max_tokens"] == 500 + 1536


def test_a_thinking_level_the_model_refuses_is_dropped_once(no_wait):
    p, sent = _gemini([(400, {"error": {"message": "Thinking level LOW is not supported for this model."}}),
                       _ok("fine")])
    assert p.complete(system="s", messages=[]).text == "fine"
    assert "reasoning_effort" in sent[0] and "reasoning_effort" not in sent[1]


def test_an_answer_lost_to_thinking_is_asked_again_with_more_room():
    p, sent = _gemini([_ok("", "length"), _ok("The bank is profitable.")])
    out = p.complete(system="s", messages=[], max_tokens=500)
    assert out.text == "The bank is profitable." and sent[1]["max_tokens"] == (500 + 1536) * 4


def test_if_it_still_writes_nothing_the_error_says_why():
    p, _ = _gemini([_ok("", "length")])
    with pytest.raises(ProviderError) as e:
        p.complete(system="s", messages=[])
    assert e.value.code == "truncated" and "thinking" in e.value.message


def test_a_partly_written_answer_reports_that_it_was_cut():
    p, _ = _gemini([_ok("The bank is making a profit of 3", "length")])
    out = p.complete(system="s", messages=[])
    assert out.finish == "length"



def test_strict_json_mode_failure_is_retried_without_it():
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        if "response_format" in sent[-1]:
            return httpx.Response(400, json={"error": {"message": "Failed to generate JSON. Please adjust "
                                                       "your prompt. See 'failed_generation' for more details.",
                                                       "code": "json_validate_failed"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"tool":"benchmarks"}'}}]})
    p = _provider(handler, brand="groq")
    p.model = "openai/gpt-oss-120b"
    out = p.complete(system="s", messages=[], json_mode=True)
    assert out.text == '{"tool":"benchmarks"}'
    assert "response_format" in sent[0] and "response_format" not in sent[1]
    assert sent[0]["reasoning_effort"] == "low"          # gpt-oss: think little


def test_other_bad_requests_still_fail():
    def handler(req):
        return httpx.Response(400, json={"error": {"message": "context length exceeded"}})
    with pytest.raises(ProviderError):
        _provider(handler, brand="groq").complete(system="s", messages=[], json_mode=True)


def test_openrouter_switches_reasoning_off():
    body = {}

    def handler(req):
        body.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    _provider(handler, brand="openrouter").complete(system="s", messages=[], max_tokens=7)
    assert body["reasoning"] == {"enabled": False}


def test_an_error_inside_a_200_is_retried_then_classified(monkeypatch):
    from app.ai.providers import openai_compat
    monkeypatch.setattr(openai_compat, "_sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"error": {"message": "Upstream error from Nvidia: "
                                                              "Service temporarily overloaded",
                                                   "code": 503}})

    with pytest.raises(ProviderError) as e:
        _provider(handler, brand="openrouter").complete(system="s", messages=[], max_tokens=7)
    assert e.value.code == "unavailable" and e.value.retryable and len(calls) == 3


def test_an_error_inside_a_200_then_an_answer(monkeypatch):
    from app.ai.providers import openai_compat
    monkeypatch.setattr(openai_compat, "_sleep", lambda s: None)
    replies = iter([httpx.Response(200, json={"error": {"message": "busy", "code": 429}}),
                    httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})])
    out = _provider(lambda req: next(replies), brand="openrouter").complete(
        system="s", messages=[], max_tokens=7)
    assert out.text == "OK"
