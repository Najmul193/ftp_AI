"""The one shape every provider is driven through."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Protocol


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str          # "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    finish: str | None = None


class ProviderError(Exception):
    """A provider failure, classified so the UI can say what to do about it.

    Codes: auth, not_found, rate_limit, timeout, unavailable, bad_request,
    refused, config.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class LLMProvider(Protocol):
    def complete(self, *, system: str, messages: list[ChatMessage],
                 max_tokens: int = 2048, json_mode: bool = False) -> Completion: ...

    def list_models(self) -> list[str]: ...


#: Model ids that are not chat models, filtered out of the picker.
_NOT_CHAT = ("embed", "whisper", "tts", "dall-e", "imagen", "image", "veo", "moderation",
             "audio", "realtime", "transcribe", "guard", "aqa", "learnlm", "davinci",
             "babbage", "search", "computer-use")


#: Model variants that are poor defaults for short structured answers:
#: previews and experiments change without notice, "lite" models follow
#: instructions less well, the rest are not general chat models.
_NOT_DEFAULT = ("preview", "exp", "lite", "thinking", "live", "audio", "vision", "learnlm",
                "gemma", "tuning", "search", "realtime", "transcribe", "instruct")
_VERSION = re.compile(r"(\d+(?:\.\d+)?)")
#: Whole words only: "gemini" contains "mini".
_FAST = re.compile(r"(?<![a-z])(flash|mini|haiku|instant)(?![a-z])")


def best_model(available: Iterable[str], preferred: Iterable[str] = ()) -> str | None:
    """The model to use when the one asked for is not offered.

    A preferred model the provider lists wins; otherwise the newest stable
    "flash"/"mini"-class model (fast and cheap, which is what these short
    calls want), otherwise the newest stable model of any kind.
    """
    models = [m for m in available if m]
    if not models:
        return None
    for m in preferred:
        if m in models:
            return m
    stable = [m for m in models if not any(x in m.lower() for x in _NOT_DEFAULT)] or models
    fast = [m for m in stable if _FAST.search(m.lower())]

    def version(m: str) -> tuple[float, int]:
        v = _VERSION.search(m)
        return (float(v.group(1)) if v else 0.0, -len(m))

    return max(fast or stable, key=version)


def chat_models(ids: list[str]) -> list[str]:
    out = {i.removeprefix("models/") for i in ids
           if i and not any(x in i.lower() for x in _NOT_CHAT)}
    return sorted(out)
