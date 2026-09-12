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

import re

# Bedrock-style trailing version markers: `-v1:0`, `:0`, `-v2:1`.
_VERSION_SUFFIX_RE = re.compile(r"(?:-v\d+)?:\d+$")

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

# BUG-C13: the same model arrives as a dozen different strings depending on
# the SDK, the gateway and the region -- `openai/gpt-4o`,
# `gpt-4o-2024-08-06`, `us.anthropic.claude-sonnet-4-5-20250929-v1:0`,
# `openrouter/anthropic/claude-sonnet-4`. Every one of those used to fall
# through to `None`, so real sessions showed no dollar figure at all.
#
# Everything below is deliberately limited to *renaming*, never repricing:
# prefixes and version suffixes are stripped, and aliases map onto a key
# that already exists in the table above. No new price point is invented
# here, so nothing in this block can make the estimate less accurate --
# only more often available.
_STRIPPABLE_PREFIXES = (
    "openai/", "anthropic/", "gemini/", "google/", "azure/", "azure_ai/",
    "models/", "bedrock/", "vertex_ai/", "openrouter/", "together_ai/",
    "groq/", "mistral/", "deepseek/", "fireworks_ai/", "ollama/",
    # Bedrock inference-profile region prefixes and its vendor-qualified ids.
    "us.", "eu.", "apac.", "anthropic.",
)

# Alternative spellings of a model already priced above. Validated at import
# time so a typo here becomes an immediate failure, not a silent None.
_ALIASES: dict[str, str] = {
    # OpenAI dated snapshots that carry the same list price as their alias.
    "gpt-4o-2024-08-06": "gpt-4o",
    "gpt-4o-2024-11-20": "gpt-4o",
    "gpt-4o-mini-2024-07-18": "gpt-4o-mini",
    "gpt-4.1-2025-04-14": "gpt-4.1",
    "gpt-4.1-mini-2025-04-14": "gpt-4.1-mini",
    "gpt-4.1-nano-2025-04-14": "gpt-4.1-nano",
    "chatgpt-4o": "gpt-4o",  # `chatgpt-4o-latest` after suffix stripping
    # Anthropic undated aliases.
    "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-sonnet-4": "claude-sonnet-4-20250514",
    "claude-opus-4": "claude-opus-4-20250514",
    "claude-3-opus": "claude-3-opus-20240229",
    "claude-3-haiku": "claude-3-haiku-20240307",
    # Google aliases.
    "gemini-2.5-pro-preview": "gemini-2.5-pro",
    "gemini-2.5-flash-preview": "gemini-2.5-flash",
}

for _alias, _target in _ALIASES.items():  # pragma: no cover - import-time guard
    if _target not in _PRICING_USD_PER_1M:
        raise RuntimeError(
            f"pricing.py: alias {_alias!r} points at {_target!r}, which is not in "
            "_PRICING_USD_PER_1M -- it would silently price as 'unknown'."
        )


def _strip_version_suffix(m: str) -> str:
    """`-v1:0` / `:0` (Bedrock) and `-latest` are packaging, not pricing."""
    if m.endswith("-latest"):
        m = m[: -len("-latest")]
    m = _VERSION_SUFFIX_RE.sub("", m)
    return m


def _normalize(model: str) -> str:
    m = model.strip().lower()
    # Gateways stack prefixes (`openrouter/anthropic/claude-sonnet-4`,
    # `bedrock/us.anthropic.claude-...`), so strip repeatedly rather than once.
    changed = True
    while changed:
        changed = False
        for prefix in _STRIPPABLE_PREFIXES:
            if m.startswith(prefix):
                m = m[len(prefix):]
                changed = True
                break
    m = _strip_version_suffix(m)
    return _ALIASES.get(m, m)


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
