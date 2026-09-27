"""One adapter for every provider that speaks the OpenAI chat-completions API.

That is most of them: OpenAI itself, Google Gemini (through its
OpenAI-compatible endpoint), Groq, OpenRouter, DeepSeek, and a local Ollama.
Plain HTTP, so none of their SDKs is a dependency.
"""

from __future__ import annotations

import httpx

from app.ai.providers.base import ChatMessage, Completion, ProviderError, chat_models


class OpenAICompatProvider:
    def __init__(self, *, base_url: str, api_key: str | None, model: str,
                 brand: str = "custom", timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.brand = brand
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if brand == "openrouter":
            headers["X-Title"] = "FTP Intelligence"
        self._http = httpx.Client(timeout=timeout, headers=headers, transport=transport)

    # ------------------------------------------------------------------ #

    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            r = self._http.request(method, f"{self.base_url}{path}", **kw)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "the provider did not answer in time",
                                retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("unavailable", f"cannot reach {self.base_url}: {exc}",
                                retryable=True) from exc
        if r.status_code >= 400:
            raise _classify(r)
        try:
            return r.json()
        except ValueError as exc:
            raise ProviderError("bad_request", "the provider returned something that is "
                                "not JSON -- is the base URL right?") from exc

    def complete(self, *, system: str, messages: list[ChatMessage],
                 max_tokens: int = 2048, json_mode: bool = False) -> Completion:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}]
                        + [{"role": m.role, "content": m.content} for m in messages],
        }
        # OpenAI's current models take max_completion_tokens and reject
        # max_tokens; the other compatible APIs still expect max_tokens.
        body["max_completion_tokens" if self.brand == "openai" else "max_tokens"] = max_tokens
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = self._call("POST", "/chat/completions", json=body)
        try:
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("bad_request", f"unexpected response shape: {str(data)[:200]}") from exc
        usage = data.get("usage") or {}
        return Completion(text=text, model=data.get("model") or self.model,
                          tokens_in=usage.get("prompt_tokens"),
                          tokens_out=usage.get("completion_tokens"),
                          finish=choice.get("finish_reason"))

    def list_models(self) -> list[str]:
        data = self._call("GET", "/models")
        items = data.get("data") or data.get("models") or []
        return chat_models([(m.get("id") or m.get("name") or "") for m in items
                            if isinstance(m, dict)])


def _classify(r: httpx.Response) -> ProviderError:
    try:
        body = r.json()
        err = body[0] if isinstance(body, list) else body
        msg = (err.get("error") or {}).get("message") if isinstance(err.get("error"), dict) \
            else err.get("error") or err.get("message")
    except Exception:  # noqa: BLE001 - any unparseable body is reported by status
        msg = None
    msg = str(msg or r.text[:200] or r.reason_phrase)
    s = r.status_code
    if s in (401, 403):
        return ProviderError("auth", f"the provider rejected the key: {msg}")
    if s == 404:
        return ProviderError("not_found", f"model or endpoint not found: {msg}")
    if s == 429:
        return ProviderError("rate_limit", f"rate limited or out of free quota: {msg}",
                             retryable=True)
    if s >= 500:
        return ProviderError("unavailable", f"provider error {s}: {msg}", retryable=True)
    return ProviderError("bad_request", f"{s}: {msg}")
