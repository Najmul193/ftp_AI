"""Known AI providers: where they live, what they need, and how they are billed.

Plain data -- no provider client is imported here -- so configuration code can
use it without reaching past the gateway. The suggested models are a starting
point only: "Connect" asks the provider for its live model list, so a model
released after this file was written is still selectable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Preset:
    brand: str
    label: str
    kind: str                   # openai_compat | anthropic
    base_url: str
    needs_key: bool
    #: The default assumption about billing. A free tier may train on prompts.
    free_tier: bool
    #: Runs on the bank's own hardware: nothing leaves the network.
    local: bool
    suggested_models: tuple[str, ...]
    key_url: str
    note: str


PRESETS: dict[str, Preset] = {p.brand: p for p in (
    Preset("gemini", "Google Gemini", "openai_compat",
           "https://generativelanguage.googleapis.com/v1beta/openai", True, True, False,
           # Google retires models for new keys; Connect falls back to the best
           # model the key actually lists (see providers.base.best_model).
           ("gemini-3.8-flash",),
           "https://aistudio.google.com/apikey",
           "Free tier through Google AI Studio. Google may use free-tier prompts to "
           "improve its products; enable billing for no-training terms."),
    Preset("groq", "Groq", "openai_compat", "https://api.groq.com/openai/v1",
           True, True, False, ("llama-3.3-70b-versatile", "openai/gpt-oss-120b"),
           "https://console.groq.com/keys",
           "Fast free tier for open models. Review data terms before sending bank data."),
    Preset("openrouter", "OpenRouter", "openai_compat", "https://openrouter.ai/api/v1",
           True, True, False, ("meta-llama/llama-3.3-70b-instruct:free",),
           "https://openrouter.ai/keys",
           "Routes to many models; ':free' models are free and may log prompts."),
    Preset("openai", "OpenAI (ChatGPT)", "openai_compat", "https://api.openai.com/v1",
           True, False, False, ("gpt-5-mini", "gpt-5"),
           "https://platform.openai.com/api-keys",
           "Paid API. API data is not used for training by default."),
    Preset("anthropic", "Anthropic Claude", "anthropic", "https://api.anthropic.com",
           True, False, False, ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"),
           "https://console.anthropic.com/settings/keys",
           "Paid API. API data is not used for training by default."),
    Preset("deepseek", "DeepSeek", "openai_compat", "https://api.deepseek.com/v1",
           True, False, False, ("deepseek-chat",), "https://platform.deepseek.com/api_keys",
           "Paid API hosted outside Bangladesh; review data residency."),
    Preset("ollama", "Ollama (on-premise)", "openai_compat", "http://localhost:11434/v1",
           False, False, True, ("qwen3:8b", "llama3.1:8b", "gemma3:12b"),
           "https://ollama.com/download",
           "Runs on the bank's own server. Nothing leaves the network."),
    Preset("custom", "Custom (OpenAI-compatible)", "openai_compat", "",
           False, False, False, (), "",
           "Any endpoint that implements the OpenAI chat-completions API "
           "(vLLM, Azure OpenAI proxy, LM Studio, ...)."),
)}


def presets_public() -> list[dict]:
    return [asdict(p) for p in PRESETS.values()]


def validate_base_url(url: str, *, production: bool) -> str:
    """Refuse URLs that are not http(s), and plain http off localhost in production.

    The server calls whatever URL an administrator enters, so this is the
    boundary that keeps it an API endpoint rather than an arbitrary fetch.
    """
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("base URL must be an http(s) URL")
    local = (u.hostname or "") in ("localhost", "127.0.0.1", "::1", "host.docker.internal")
    if production and u.scheme == "http" and not local:
        raise ValueError("plain http is only allowed to localhost in production")
    return url.strip().rstrip("/")
