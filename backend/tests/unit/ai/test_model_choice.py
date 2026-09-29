"""Connecting a key whose provider has retired the suggested model."""

from __future__ import annotations

import json

import httpx

from app.ai.gateway import gateway as gw
from app.ai.providers.base import best_model

GEMINI_LIST = ["models/gemini-3.8-flash", "models/gemini-3.8-flash-lite", "models/gemini-3.8-pro",
               "models/gemini-3.9-flash-preview", "models/gemini-embedding-002",
               "models/gemma-4-27b-it", "models/gemini-3.5-flash"]


def test_a_listed_preferred_model_wins():
    assert best_model(["a-1", "b-2"], ("b-2", "a-1")) == "b-2"


def test_the_newest_stable_fast_model_is_chosen_otherwise():
    ids = [m.removeprefix("models/") for m in GEMINI_LIST]
    assert best_model(ids, ("gemini-2.5-flash",)) == "gemini-3.8-flash"


def test_without_a_fast_model_the_newest_stable_one():
    assert best_model(["llama-3.1-70b", "llama-3.3-70b", "llama-4-scout-preview"]) == "llama-3.3-70b"
    assert best_model([]) is None


def test_connect_tests_a_model_the_key_offers_when_the_default_is_retired(monkeypatch):
    pinged = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in GEMINI_LIST]})
        body = json.loads(req.content)
        pinged.append(body["model"])
        if body["model"] == "gemini-2.5-flash":
            return httpx.Response(404, json={"error": {"message": "no longer available"}})
        return httpx.Response(200, json={"model": body["model"], "choices": [
            {"message": {"content": "OK"}, "finish_reason": "stop"}]})

    import app.ai.gateway.gateway as g
    from app.ai.providers.openai_compat import OpenAICompatProvider

    def build(*, brand, kind, base_url, api_key, model, timeout=60.0):
        return OpenAICompatProvider(base_url=base_url, api_key=api_key, model=model, brand=brand,
                                    timeout=timeout, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(g, "build", build)
    monkeypatch.setattr(g, "_log", lambda **row: 1)

    r = gw.probe(caller=gw.SYSTEM_CALLER, brand="gemini", kind="openai_compat",
                 base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                 api_key="k", model="gemini-2.5-flash", preferred=("gemini-2.5-flash",))
    assert r.ok and r.model == "gemini-3.8-flash" and pinged == ["gemini-3.8-flash"]


def test_gemini_is_not_a_mini_model():
    assert best_model(["gemini-3.8-pro", "gemini-3.8-flash"]) == "gemini-3.8-flash"
    assert best_model(["gpt-5", "gpt-5-mini"]) == "gpt-5-mini"
