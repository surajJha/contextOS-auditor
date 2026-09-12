"""Regression tests for the report-layer honesty bugs (R2/R3/R4/R6/R7).

Every bug here is a case of the UI stating something the underlying data
does not support. None of them crash, which is exactly why they survived --
they just quietly print a wrong number or an untrue promise.
"""

from __future__ import annotations

from contextos_auditor import report
from contextos_auditor._internal.pricing import estimate_usd
from contextos_auditor.report import (
    _cta_usd_line,
    _turn_label,
    render_html,
    render_terminal,
)

META = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}


def _result(**over):
    base = {
        "actual": {
            "prompt_tokens": 10_000,
            "completion_tokens": 2_000,
            "total_tokens": 12_000,
            "total_nano_aiu": 0,
            "estimated_usd": 0.05,
        },
        "kit_estimate": {
            "total_nano_aiu": 0,
            "write_waste_tokens": 0,
            "duplicate_context_waste_tokens": 4_000,
            "waste_tokens": 4_000,
            "label": "estimated",
            "basis": "tokens",
        },
        "pricing": {"source": "x", "snapshot_date": "2025-01-01"},
        "save_pct": 33.3,
        "writes": [],
        "duplicate_reads": [],
        "turns": [
            {"turn": 1, "prompt_tokens": 10_000, "completion_tokens": 2_000, "total_tokens": 12_000}
        ],
        "levers_fired": [],
        "disclaimer": "Estimated opportunity only.",
    }
    base.update(over)
    return base


# --------------------------------------------------------------- R2


def test_r2_waste_is_priced_at_the_input_rate_not_a_share_of_the_blended_total():
    """The old code did `total_usd * (save_pct/100)`. Waste is duplicated
    *prompt* context, and input tokens are far cheaper than output tokens,
    so scaling the blended total overstates the dollar saving."""
    result = _result()
    line = _cta_usd_line(result, "gpt-4o")
    assert line is not None

    expected = estimate_usd("gpt-4o", 4_000, 0)
    assert expected is not None
    assert f"~${expected:.4f}" in line

    naive = 0.05 * (33.3 / 100.0)  # what the buggy version produced
    assert abs(expected - naive) > 1e-6, "test no longer distinguishes the two"
    assert f"~${naive:.4f}" not in line


def test_r2_unpriced_model_says_nothing_rather_than_guessing():
    assert _cta_usd_line(_result(), "some-unlisted-model") is None
    assert _cta_usd_line(_result(), None) is None


def test_r2_zero_waste_produces_no_dollar_claim():
    result = _result()
    result["kit_estimate"]["waste_tokens"] = 0
    assert _cta_usd_line(result, "gpt-4o") is None


def test_r2_terminal_and_html_quote_the_same_dollar_figure():
    """R1's lesson: one number, one source. The two renderers must not
    diverge."""
    result = _result()
    expected = estimate_usd("gpt-4o", 4_000, 0)
    assert expected is not None
    needle = f"${expected:.4f}"
    assert needle in render_terminal("s", META, result)
    assert needle in render_html("s", META, result, poll_seconds=1.0)


# --------------------------------------------------------------- R3


def test_r3_sub_precision_saving_is_not_rendered_as_a_hard_zero():
    """`~$0.0000` beside a positive percentage reads like a broken number
    and discredits the rest of the page."""
    result = _result()
    result["kit_estimate"]["waste_tokens"] = 1  # a fraction of a cent
    line = _cta_usd_line(result, "gpt-4o")
    assert line is not None
    assert "~$0.0000" not in line
    assert "<$0.0001" in line


# --------------------------------------------------------------- R6


def test_r6_unknown_turn_renders_a_marker_not_the_string_none():
    assert _turn_label(None) == "?"
    assert _turn_label("") == "?"
    assert _turn_label(0) == "0"
    assert _turn_label(7) == "7"


def test_r6_duplicate_read_without_a_turn_does_not_print_turn_none():
    result = _result(
        duplicate_reads=[
            {"turn": None, "path": "a.py", "tokens": 100, "carried": 2, "waste_tokens": 200}
        ]
    )
    out = render_html("s", META, result, poll_seconds=1.0)
    assert "turn None" not in out
    assert "turn ?" in out


# --------------------------------------------------------------- R7


def _turns_totals_only():
    return [
        {"turn": 1, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 900},
        {"turn": 2, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 1500},
    ]


def test_r7_chart_is_not_empty_when_the_framework_reports_only_totals():
    """The trace rows print `total_tokens`; the chart used to plot
    prompt+completion, so a totals-only framework got real numbers in the
    rows beside a chart of zero-height bars."""
    result = _result(turns=_turns_totals_only())
    out = render_html("s", META, result, poll_seconds=1.0)
    assert "<svg" in out
    assert "total: 1500 tokens" in out
    assert "split not reported" in out
    # No bar may be flat when its turn genuinely has tokens.
    assert 'height="0.00"' not in out


def test_r7_prompt_completion_split_is_still_shown_when_reported():
    result = _result(
        turns=[
            {"turn": 1, "prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1000},
        ]
    )
    out = render_html("s", META, result, poll_seconds=1.0)
    assert "prompt: 800 tokens" in out
    assert "completion: 200 tokens" in out
    assert "split not reported" not in out


# --------------------------------------------------------------- R4


def test_r4_static_export_carries_no_meta_refresh_and_no_refresh_footer():
    out = render_html("s", META, _result(), poll_seconds=2.0, auto_refresh=False)
    assert "http-equiv=\"refresh\"" not in out
    assert "auto-refreshes every" not in out


def test_r4_polling_export_still_advertises_its_refresh():
    out = render_html("s", META, _result(), poll_seconds=2.0, auto_refresh=True)
    assert "http-equiv=\"refresh\"" in out
    assert "auto-refreshes every 2s" in out


def test_r4_report_html_export_is_static(tmp_path, monkeypatch):
    """`report --html` writes the file exactly once; a meta-refresh there
    reloads identical bytes forever and the footer promises updates that
    will never come."""
    captured = {}

    def fake_render(session_id, meta, result, poll_seconds, *, auto_refresh=True):
        captured["auto_refresh"] = auto_refresh
        return "<html></html>"

    monkeypatch.setattr(report, "render_html", fake_render)
    from contextos_auditor import cli

    monkeypatch.setattr(cli, "render_html", fake_render)
    cli._write_html_snapshot(
        str(tmp_path / "out.html"), "s", META, _result(), poll_seconds=2.0
    )
    assert captured["auto_refresh"] is False
