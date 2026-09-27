"""The one shape every provider is driven through."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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


def chat_models(ids: list[str]) -> list[str]:
    out = {i.removeprefix("models/") for i in ids
           if i and not any(x in i.lower() for x in _NOT_CHAT)}
    return sorted(out)
