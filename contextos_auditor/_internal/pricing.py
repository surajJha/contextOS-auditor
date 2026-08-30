"""AUD-011: optional, dated, sourced $ estimate alongside the always-real
token counts.

Every comparable tool (LangSmith, Langfuse, Helicone, Arize Phoenix,
AgentOps) shows $ cost as a headline metric. contextos-auditor never
invents a number without a citable source, so this module is deliberately
narrow: a small, hand-curated snapshot of *list* prices for a handful of
current, popular models, pulled from a single publicly maintained source
(litellm's `model_prices_and_context_window.json`, itself sourced from
each provider's own pricing page) on the date below. If a session's model
isn't in this table, `estimate_usd()` returns `None` -- the UI shows
"no dated pricing available for this model", never a guess.

This is explicitly NOT a live pricing feed and NOT your actual bill:
- providers change prices without notice; this snapshot will go stale
- volume discounts, enterprise agreements, and cached-token discounts are
  not modeled
- if your organization has a negotiated rate, it will differ from this

PRICING_SNAPSHOT_DATE below is the single source of truth for how stale
this table might be; bump it (and the numbers) whenever it's refreshed.
"""

from __future__ import annotations

# Source: https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json
# (community-maintained, sourced from each provider's own pricing page),
# snapshot taken on the date below. $ per 1,000,000 tokens.
PRICING_SNAPSHOT_DATE = "2026-08-30"
PRICING_SOURCE_URL = (
    "https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json"
)

# model name (as commonly passed to the SDK) -> (input $/1M, output $/1M)
_PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.4),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-3.5-turbo": (0.5, 1.5),
    "o1": (15.0, 60.0),
    "o3-mini": (1.1, 4.4),
    # Anthropic
    "claude-opus-4-20250514": (15.0, 75.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-3-opus-20240229": (15.0, 75.0),
    "claude-3-haiku-20240307": (0.25, 1.25),
    # Google
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.3, 2.5),
    "gemini-2.0-flash": (0.1, 0.4),
    # DeepSeek
    "deepseek-chat": (0.28, 0.42),
    "deepseek-reasoner": (0.28, 0.42),
}

# Common prefixes SDKs/adapters prepend that aren't part of the model name
# itself (e.g. crewai's `LLM(model="openai/gpt-5-mini")`).
_STRIPPABLE_PREFIXES = ("openai/", "anthropic/", "gemini/", "google/", "azure/", "models/")


def _normalize(model: str) -> str:
    m = model.strip().lower()
    for prefix in _STRIPPABLE_PREFIXES:
        if m.startswith(prefix):
            m = m[len(prefix):]
            break
    return m


def estimate_usd(model: str | None, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Returns an estimated $ cost for this call, or None if `model` isn't
    in the dated pricing snapshot -- never a guessed/interpolated number."""
    if not model:
        return None
    prices = _PRICING_USD_PER_1M.get(_normalize(model))
    if prices is None:
        return None
    input_rate, output_rate = prices
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


def has_pricing(model: str | None) -> bool:
    return bool(model) and _normalize(model) in _PRICING_USD_PER_1M
