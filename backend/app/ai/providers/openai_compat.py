"""One adapter for every provider that speaks the OpenAI chat-completions API.

That is most of them: OpenAI itself, Google Gemini (through its
OpenAI-compatible endpoint), Groq, OpenRouter, DeepSeek, and a local Ollama.
Plain HTTP, so none of their SDKs is a dependency.
"""

from __future__ import annotations

import re
import time

import httpx

from app.ai.providers.base import ChatMessage, Completion, ProviderError, chat_models


#: Reasoning models served locally (Qwen3, DeepSeek-R1) may write their
#: working inline before the answer.
_THINK = re.compile(r"<think>.*?</think>\s*", re.S)


#: How hard a reasoning model may think, per brand. Ollama (Qwen3) takes
#: "none"; Gemini's thinking models refuse "minimal" and some refuse "none",
#: so it gets "low" -- and a rejected level is retried without the setting.
REASONING_EFFORT = {"ollama": "none", "gemini": "low"}
#: Extra token room where thinking counts against the reply's limit.
THINKING_HEADROOM = {"gemini": 1536}
MAX_TOKENS_CEILING = 8192

#: Waits before the second and third attempt at a busy provider. Free tiers
#: (Gemini especially) answer 503 "high demand" often and briefly.
RETRY_WAITS = (1.5, 4.0)
#: A Retry-After longer than this is a quota, not a blip: do not wait for it.
#: Per-minute token limits (Groq's free tier) ask for 10-20 s; waiting beats failing.
MAX_RETRY_AFTER = 20.0
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_sleep = time.sleep


def _embedded_status(r: httpx.Response) -> int | None:
    """The status in a 200 response whose body is an error and no answer."""
    if r.status_code != 200:
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    if not isinstance(d, dict) or "choices" in d or not isinstance(d.get("error"), dict):
        return None
    try:
        code = int(d["error"].get("code") or 502)
    except (TypeError, ValueError):
        code = 502
    return code if 400 <= code < 600 else 502


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return float(r.headers.get("retry-after", ""))
    except ValueError:
        return None


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
        """One request, retried a couple of times when the provider is busy.

        A timeout is not retried: it has already cost the whole timeout, and a
        second one would double the wait. Nor is a Retry-After longer than a
        few seconds, which means a used-up quota rather than a busy moment.
        """
        for attempt in range(len(RETRY_WAITS) + 1):
            last = attempt == len(RETRY_WAITS)
            try:
                r = self._http.request(method, f"{self.base_url}{path}", **kw)
            except httpx.TimeoutException as exc:
                raise ProviderError("timeout", "the provider did not answer in time",
                                    retryable=True) from exc
            except httpx.HTTPError as exc:
                if not last:
                    _sleep(RETRY_WAITS[attempt])
                    continue
                raise ProviderError("unavailable", f"cannot reach {self.base_url}: {exc}",
                                    retryable=True) from exc
            if r.status_code in _RETRY_STATUS and not last:
                wait = _retry_after(r)
                if wait is None or wait <= MAX_RETRY_AFTER:
                    _sleep(max(wait or 0.0, RETRY_WAITS[attempt]))
                    continue
            # OpenRouter reports an upstream failure ("Upstream error from
            # Nvidia: Service temporarily overloaded") as 200 with an error
            # body: read it as the status it names, so it is retried and
            # explained rather than taken for a garbled reply.
            embedded = _embedded_status(r)
            if embedded is not None:
                # The body is already decoded: carry it, not its encoding headers.
                r = httpx.Response(embedded, content=r.content, request=r.request,
                                   headers={"content-type": "application/json"})
                if embedded in _RETRY_STATUS and not last:
                    _sleep(RETRY_WAITS[attempt])
                    continue
            break
        if r.status_code >= 400:
            err = _classify(r)
            if r.status_code in _RETRY_STATUS:
                err.message += f" (tried {attempt + 1} time{'s' if attempt else ''})"
            raise err
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
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        # Reasoning models think before they answer, and on some endpoints the
        # thinking counts against the same token limit as the answer: a short
        # limit is spent thinking and the reply comes back empty or cut off.
        # Every call here wants a short, structured answer, so thinking is
        # kept to a minimum and the limit is given room for what remains.
        effort = REASONING_EFFORT.get(self.brand) or (
            "low" if "gpt-oss" in self.model.lower() else None)
        if effort:
            body["reasoning_effort"] = effort
        if self.brand == "openrouter":
            # OpenRouter's own switch. Some reasoning models it routes to
            # (NVIDIA Nemotron) otherwise write their thinking into the answer
            # itself, spending the whole allowance before the reply begins.
            body["reasoning"] = {"enabled": False}
        limit = max_tokens + THINKING_HEADROOM.get(self.brand, 0)

        for attempt in range(2):
            body["max_completion_tokens" if self.brand == "openai" else "max_tokens"] = limit
            try:
                data = self._call("POST", "/chat/completions", json=body)
            except ProviderError as exc:
                low = exc.message.lower()
                # A model that does not take this thinking level: once, without it.
                if exc.code == "bad_request" and ("reasoning_effort" in body or "reasoning" in body) \
                        and any(w in low for w in ("thinking", "reasoning")):
                    # A model that cannot switch thinking off: think a little.
                    body.pop("reasoning_effort", None)
                    if "reasoning" in body:
                        body["reasoning"] = {"effort": "low"}
                    data = self._call("POST", "/chat/completions", json=body)
                # Strict JSON mode (Groq) rejects a reply that is not perfectly
                # valid JSON. The caller extracts and validates the JSON itself,
                # so ask once more without the strict mode.
                elif exc.code == "bad_request" and "response_format" in body and \
                        any(w in low for w in ("generate json", "json_validate", "failed_generation",
                                               "response_format", "json mode")):
                    del body["response_format"]
                    data = self._call("POST", "/chat/completions", json=body)
                else:
                    raise
            try:
                choice = data["choices"][0]
                text = _THINK.sub("", choice["message"].get("content") or "").strip()
            except (KeyError, IndexError, TypeError) as exc:
                raise ProviderError("bad_request",
                                    f"unexpected response shape: {str(data)[:200]}") from exc
            finish = choice.get("finish_reason")
            # Nothing but thinking: once more with a much larger limit.
            if not text and finish == "length" and attempt == 0:
                limit = min(limit * 4, MAX_TOKENS_CEILING)
                continue
            break
        if not text and finish == "length":
            raise ProviderError("truncated", "the model used its whole allowance thinking and "
                                             "wrote no answer; try again, or choose a model "
                                             "that does not reason at length", retryable=True)
        usage = data.get("usage") or {}
        return Completion(text=text, model=data.get("model") or self.model,
                          tokens_in=usage.get("prompt_tokens"),
                          tokens_out=usage.get("completion_tokens"),
                          finish=finish)

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
    if s == 503:
        return ProviderError("unavailable", f"the provider is overloaded right now: {msg}",
                             retryable=True)
    if s >= 500:
        return ProviderError("unavailable", f"provider error {s}: {msg}", retryable=True)
    return ProviderError("bad_request", f"{s}: {msg}")
