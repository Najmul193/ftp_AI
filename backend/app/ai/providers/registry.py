"""Build a provider client from its stored configuration. Gateway-only."""

from __future__ import annotations

from app.ai.providers.base import LLMProvider, ProviderError
from app.ai.providers.claude import AnthropicProvider
from app.ai.providers.openai_compat import OpenAICompatProvider


def build(*, brand: str, kind: str, base_url: str, api_key: str | None, model: str,
          timeout: float = 60.0) -> LLMProvider:
    if kind == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model, base_url=base_url,
                                 timeout=timeout)
    if kind == "openai_compat":
        return OpenAICompatProvider(base_url=base_url, api_key=api_key, model=model,
                                    brand=brand, timeout=timeout)
    raise ProviderError("config", f"unknown provider kind {kind!r}")
