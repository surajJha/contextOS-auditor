"""BUG-C13: the pricing table missed nearly every real-world model string.

The same model reaches the auditor as `gpt-4o`, `openai/gpt-4o`,
`gpt-4o-2024-08-06` or `us.anthropic.claude-...-v1:0` depending on the SDK,
gateway and region. Each miss silently removed the dollar figure from the
report -- the single number users care about most.

The fix only renames; it never reprices. These tests pin both halves of
that: more strings resolve, and no string resolves to a *different* price.
"""

from __future__ import annotations

import pytest

from contextos_auditor._internal.pricing import (
    _ALIASES,
    _PRICING_USD_PER_1M,
    _normalize,
    estimate_usd,
    has_pricing,
)

RESOLVES = [
    ("gpt-4o", "gpt-4o"),
    ("openai/gpt-4o-mini", "gpt-4o-mini"),
    ("  GPT-4O  ", "gpt-4o"),
    ("gpt-4o-2024-08-06", "gpt-4o"),
    ("gpt-4o-2024-11-20", "gpt-4o"),
    ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"),
    ("gpt-4.1-mini-2025-04-14", "gpt-4.1-mini"),
    ("chatgpt-4o-latest", "gpt-4o"),
    ("claude-sonnet-4-5", "claude-sonnet-4-5-20250929"),
    ("claude-sonnet-4-5-latest", "claude-sonnet-4-5-20250929"),
    ("anthropic/claude-sonnet-4-5-20250929", "claude-sonnet-4-5-20250929"),
    ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4-5-20250929"),
    ("bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5-20251001"),
    ("eu.anthropic.claude-sonnet-4-20250514-v1:0", "claude-sonnet-4-20250514"),
    ("openrouter/anthropic/claude-sonnet-4", "claude-sonnet-4-20250514"),
    ("claude-3-opus-latest", "claude-3-opus-20240229"),
    ("gemini/gemini-2.5-flash", "gemini-2.5-flash"),
    ("vertex_ai/gemini-2.5-pro", "gemini-2.5-pro"),
    ("models/gemini-2.0-flash", "gemini-2.0-flash"),
]


@pytest.mark.parametrize("raw,canonical", RESOLVES, ids=[r for r, _ in RESOLVES])
def test_real_world_model_strings_resolve_to_a_priced_model(raw, canonical):
    assert _normalize(raw) == canonical
    assert has_pricing(raw), f"{raw!r} would render no dollar figure"
    assert estimate_usd(raw, 1_000_000, 0) == estimate_usd(canonical, 1_000_000, 0)


def test_every_alias_targets_a_real_entry():
    """An alias pointing at a missing key prices as 'unknown' silently."""
    for alias, target in _ALIASES.items():
        assert target in _PRICING_USD_PER_1M, f"{alias} -> {target} is dangling"


def test_aliases_never_introduce_a_new_price_point():
    """The whole safety argument for C13 is that it renames rather than
    reprices. If an alias ever maps to a price not already in the table,
    that argument no longer holds."""
    known = set(_PRICING_USD_PER_1M.values())
    for alias, target in _ALIASES.items():
        assert _PRICING_USD_PER_1M[target] in known, alias


def test_unknown_models_still_return_none_rather_than_a_guess():
    for unknown in (
        "groq/llama-3.1-70b",
        "some-random-model",
        "my-azure-deployment-name",
        "",
        None,
    ):
        assert estimate_usd(unknown, 1000, 1000) is None
        assert has_pricing(unknown) is False


def test_normalisation_cannot_strip_a_name_down_to_a_wrong_model():
    """Stripping is prefix/suffix only -- it must never turn one real model
    into a different real one."""
    assert _normalize("gpt-4o-mini") == "gpt-4o-mini"  # not "gpt-4o"
    assert estimate_usd("gpt-4o-mini", 1_000_000, 0) != estimate_usd("gpt-4o", 1_000_000, 0)


def test_input_and_output_tokens_are_priced_at_different_rates():
    """R2 depends on this being true; if a model ever had equal rates the
    R2 regression test would silently stop proving anything."""
    inp = estimate_usd("gpt-4o", 1_000_000, 0)
    out = estimate_usd("gpt-4o", 0, 1_000_000)
    assert inp is not None and out is not None
    assert inp != out
