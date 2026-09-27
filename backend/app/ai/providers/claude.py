"""Claude, through the official Anthropic SDK.

The key is always passed explicitly: the SDK would otherwise fall back to
whatever credentials the host happens to have (an environment variable, a CLI
profile), and a gateway must only ever use the key an administrator installed.

Claude Opus 5 and later run safety classifiers that can decline a request
(`stop_reason == "refusal"`). For those models the request opts into
server-side fallbacks (`fallbacks: "default"`), so a decline is retried on
Anthropic's recommended model inside the same call instead of failing.
"""

from __future__ import annotations

import anthropic

from app.ai.providers.base import ChatMessage, Completion, ProviderError, chat_models

#: Models that carry refusal classifiers and accept server-side fallbacks.
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    def __init__(self, *, api_key: str | None, model: str,
                 base_url: str | None = None, timeout: float = 60.0):
        if not api_key:
            raise ProviderError("config", "an Anthropic API key is required")
        self.model = model
        kw: dict = {"api_key": api_key, "timeout": timeout, "max_retries": 2}
        if base_url and base_url.rstrip("/") != "https://api.anthropic.com":
            kw["base_url"] = base_url
        self._client = anthropic.Anthropic(**kw)

    def complete(self, *, system: str, messages: list[ChatMessage],
                 max_tokens: int = 2048, json_mode: bool = False) -> Completion:
        params: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system + ("\n\nRespond with a single JSON object and nothing else."
                                if json_mode else ""),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        try:
            if self.model.startswith(_FALLBACK_MODELS):
                resp = self._client.beta.messages.create(
                    **params, betas=[_FALLBACK_BETA], fallbacks="default")
            else:
                resp = self._client.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise ProviderError("auth", f"the provider rejected the key: {exc.message}") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError("auth", f"the key lacks permission: {exc.message}") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError("not_found", f"model not found: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError("rate_limit", f"rate limited: {exc.message}", retryable=True) from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError("bad_request", exc.message) from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderError("timeout", "Claude did not answer in time", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError("unavailable", f"provider error {exc.status_code}: {exc.message}",
                                retryable=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("unavailable", "cannot reach the Anthropic API",
                                retryable=True) from exc

        # A refusal is a 200 with no usable content: check before reading.
        if resp.stop_reason == "refusal":
            raise ProviderError("refused", "the model declined this request")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return Completion(text=text, model=resp.model,
                          tokens_in=resp.usage.input_tokens,
                          tokens_out=resp.usage.output_tokens,
                          finish=resp.stop_reason)

    def list_models(self) -> list[str]:
        try:
            return chat_models([m.id for m in self._client.models.list()])
        except anthropic.AuthenticationError as exc:
            raise ProviderError("auth", f"the provider rejected the key: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("unavailable", "cannot reach the Anthropic API",
                                retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError("unavailable", f"provider error {exc.status_code}: {exc.message}") from exc
