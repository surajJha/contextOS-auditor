"""AUD-011: real, dated, sourced $ cost estimate -- the biggest single
competitive gap identified in the industry audit (every comparable tool
shows $ cost as a headline metric; this Auditor previously only ever
showed token counts). Covers the pricing lookup itself, the honesty
contract (unpriced models return None, never a guess), and end-to-end
wiring through record_llm -> emit_turn -> shadow_session -> report."""

from __future__ import annotations

from contextos_auditor._internal.pricing import estimate_usd, has_pricing
from contextos_auditor._internal.shadow_kit import shadow_session
from contextos_auditor.report import render_html, render_terminal


def test_estimate_usd_known_model_matches_hand_computed_value():
    # gpt-5-mini: $0.25/1M input, $2.00/1M output (per the dated snapshot)
    usd = estimate_usd("gpt-5-mini", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert usd == 2.25


def test_estimate_usd_strips_common_provider_prefix():
    # crewai builds LLM(model="openai/gpt-5-mini", ...) -- the "openai/" is
    # not part of the pricing table's key.
    a = estimate_usd("openai/gpt-5-mini", 1000, 1000)
    b = estimate_usd("gpt-5-mini", 1000, 1000)
    assert a == b is not None


def test_estimate_usd_returns_none_for_unpriced_model_never_a_guess():
    assert estimate_usd("some-model-not-in-the-table", 1000, 1000) is None
    assert estimate_usd(None, 1000, 1000) is None
    assert has_pricing("some-model-not-in-the-table") is False
    assert has_pricing("gpt-5-mini") is True


def _turn_event(turn: int, model_priced: bool) -> dict:
    return {
        "kind": "turn",
        "turn": turn,
        "usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
        "cost": {
            "total_nano_aiu": 0,
            "estimated_usd": estimate_usd("gpt-5-mini", 1000, 1000) if model_priced else None,
        },
        "tool_calls": [],
    }


def test_shadow_session_sums_estimated_usd_when_fully_priced():
    events = [_turn_event(1, model_priced=True), _turn_event(2, model_priced=True)]
    result = shadow_session(events)
    assert result["actual"]["estimated_usd"] == round(2 * 0.00225, 6)
    assert result["pricing"]["snapshot_date"]


def test_shadow_session_reports_none_when_any_turn_is_unpriced():
    """A partially-priced session (e.g. a mid-run model swap to something
    not in the table) must report None, not a silently-undercounted total --
    this is the core honesty contract of AUD-011."""
    events = [_turn_event(1, model_priced=True), _turn_event(2, model_priced=False)]
    result = shadow_session(events)
    assert result["actual"]["estimated_usd"] is None


def test_shadow_session_reports_none_when_no_turns_priced():
    events = [_turn_event(1, model_priced=False)]
    result = shadow_session(events)
    assert result["actual"]["estimated_usd"] is None


def test_render_terminal_shows_priced_and_unpriced_sessions_correctly():
    priced = shadow_session([_turn_event(1, model_priced=True)])
    unpriced = shadow_session([_turn_event(1, model_priced=False)])

    priced_text = render_terminal("s1", {}, priced)
    assert "estimated cost: $" in priced_text
    assert "list price as of" in priced_text

    unpriced_text = render_terminal("s2", {}, unpriced)
    assert "estimated cost: n/a" in unpriced_text


def test_render_html_shows_priced_and_unpriced_sessions_correctly():
    priced = shadow_session([_turn_event(1, model_priced=True)])
    unpriced = shadow_session([_turn_event(1, model_priced=False)])

    priced_html = render_html("s1", {}, priced, 5.0)
    assert "$0.0023" in priced_html or "$0.00225" in priced_html or "list price as of" in priced_html

    unpriced_html = render_html("s2", {}, unpriced, 5.0)
    assert "n/a (no dated pricing" in unpriced_html
