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
    assert seen["body"]["max_tokens"] == 50


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
    _provider(handler).complete(system="s", messages=[])
    assert "reasoning_effort" not in seen
