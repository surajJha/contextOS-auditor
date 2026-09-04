"""LNCH-012/015/016/017: dashboard enhancements over the existing
trace/CTA rendering --

  - LNCH-012: per-tool call count / attributable waste / share table
    ("top offenders"), in both `render_terminal` and `render_html`.
  - LNCH-015: hand-rolled inline SVG bar chart of prompt/completion
    tokens per turn, `render_html` only.
  - LNCH-016: a "Download JSON" export button carrying the full result
    as a base64 `data:` URI.
  - LNCH-017: distinct empty/error states instead of a wall of zeros.

None of these touch `shadow_session()`'s waste arithmetic -- they only
read/aggregate fields it already returns.
"""

from __future__ import annotations

import base64
import json

from contextos_auditor._internal.audit_emit import load_events
from contextos_auditor._internal.base import FrameworkAuditSession
from contextos_auditor._internal.shadow_kit import shadow_session
from contextos_auditor.report import (
    NO_TURNS_MARKER,
    NO_WASTE_MARKER,
    SESSION_ERROR_MARKER,
    _build_tool_breakdown,
    _export_button_html,
    _tokens_svg,
    render_html,
    render_terminal,
)


def _wasteful_result(tmp_path):
    """A session with both write-hunk waste and duplicate-read waste,
    across several turns and several distinct tools, so the per-tool
    breakdown has more than one non-zero row."""
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-4o", task="t", out_dir=tmp_path,
    )
    lines = ["def total(x):", "    return x", ""] * 200
    old = "\n".join(lines)
    new_lines = list(lines)
    new_lines[0] = "def total(x, y=1):"
    new = "\n".join(new_lines)

    session.record_tool("list_dir", {"path": "."}, "billing.py\n")
    session.record_tool("read_file", {"path": "billing.py"}, old)
    session.record_llm({"prompt_tokens": 1200, "completion_tokens": 220}, "gpt-4o")

    # Re-read of the same content -> duplicate-context waste, attributed
    # to read_file.
    session.record_tool("read_file", {"path": "billing.py"}, old)
    session.record_llm({"prompt_tokens": 1300, "completion_tokens": 90}, "gpt-4o")

    # Whole-file rewrite -> write-hunk waste, attributed to write_file.
    session.record_tool("write_file", {"path": "billing.py", "content": new}, "wrote")
    session.record_llm({"prompt_tokens": 3000, "completion_tokens": 220}, "gpt-4o")

    session.finish(success=True)
    events = load_events(tmp_path / session.session_id)
    return session.session_id, shadow_session(events)


def _clean_result(tmp_path):
    """Turns happened, but nothing wasteful -- a single fresh read, no
    writes, no re-reads."""
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-4o", task="t", out_dir=tmp_path,
    )
    session.record_tool("read_file", {"path": "small.py"}, "x = 1\n")
    session.record_llm({"prompt_tokens": 5, "completion_tokens": 3}, "gpt-4o")
    session.finish(success=True)
    events = load_events(tmp_path / session.session_id)
    return session.session_id, shadow_session(events)


def _many_turn_result(tmp_path, n=120):
    """A session with `n` turns and no tool calls, purely to exercise the
    SVG chart's many-turn degrade path -- content of the turns doesn't
    matter here, only that there are a lot of them."""
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-4o", task="t", out_dir=tmp_path,
    )
    for i in range(n):
        session.record_llm({"prompt_tokens": 10 + i, "completion_tokens": 5}, "gpt-4o")
    session.finish(success=True)
    events = load_events(tmp_path / session.session_id)
    return session.session_id, shadow_session(events)


# ---------------------------------------------------------------------------
# LNCH-012: per-tool breakdown
# ---------------------------------------------------------------------------


def test_tool_breakdown_aggregates_count_and_attributable_waste(tmp_path):
    _, result = _wasteful_result(tmp_path)
    rows = _build_tool_breakdown(result)
    by_tool = {r["tool"]: r for r in rows}

    assert by_tool["read_file"]["count"] == 2
    assert by_tool["write_file"]["count"] == 1
    assert by_tool["list_dir"]["count"] == 1

    # Waste is only attributable to read_file (duplicate reads) and
    # write_file (whole-file rewrites); list_dir gets a row but 0 tokens.
    assert by_tool["read_file"]["tokens"] == result["kit_estimate"]["duplicate_context_waste_tokens"]
    assert by_tool["write_file"]["tokens"] == result["kit_estimate"]["write_waste_tokens"]
    assert by_tool["list_dir"]["tokens"] == 0

    total_waste = result["kit_estimate"]["waste_tokens"]
    for r in rows:
        expected_share = (r["tokens"] / total_waste * 100.0) if total_waste else 0.0
        assert abs(r["share"] - expected_share) < 1e-9

    # Sorted by attributable cost descending.
    assert rows == sorted(rows, key=lambda r: r["tokens"], reverse=True)


def test_tool_breakdown_empty_when_no_tool_calls():
    result = shadow_session([])
    assert _build_tool_breakdown(result) == []


def test_render_terminal_shows_top_offenders_table(tmp_path):
    session_id, result = _wasteful_result(tmp_path)
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}
    out = render_terminal(session_id, meta, result)
    assert "top offenders" in out
    assert "read_file" in out
    assert "write_file" in out


def test_render_html_shows_top_offenders_table(tmp_path):
    session_id, result = _wasteful_result(tmp_path)
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}
    out = render_html(session_id, meta, result, poll_seconds=1.0)
    assert 'class="offenders"' in out
    assert "Top offenders" in out
    assert "read_file" in out
    assert "write_file" in out


# ---------------------------------------------------------------------------
# LNCH-015: inline SVG tokens-per-turn chart
# ---------------------------------------------------------------------------


def test_svg_renders_for_single_turn_session(tmp_path):
    session_id, result = _clean_result(tmp_path)
    assert len(result["turns"]) == 1
    svg = _tokens_svg(result)
    assert "<svg" in svg and "</svg>" in svg
    assert svg.count("<rect") >= 1


def test_svg_renders_for_many_turn_session(tmp_path):
    _, result = _many_turn_result(tmp_path, n=150)
    assert len(result["turns"]) == 150
    svg = _tokens_svg(result)  # must not raise
    assert "<svg" in svg and "</svg>" in svg


def test_svg_empty_for_zero_turn_session():
    result = shadow_session([])
    assert _tokens_svg(result) == ""


def test_render_html_embeds_chart_only_when_turns_exist(tmp_path):
    session_id, result = _clean_result(tmp_path)
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}
    out = render_html(session_id, meta, result, poll_seconds=1.0)
    assert "<svg" in out

    empty_meta = {"framework": "crewai", "model": "gpt-4o", "status": "running"}
    empty_out = render_html("empty", empty_meta, shadow_session([]), poll_seconds=1.0)
    assert "<svg" not in empty_out


def test_render_terminal_has_no_svg_markup(tmp_path):
    """LNCH-015 is HTML-only -- the terminal keeps its plain text table."""
    session_id, result = _wasteful_result(tmp_path)
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}
    out = render_terminal(session_id, meta, result)
    assert "<svg" not in out


# ---------------------------------------------------------------------------
# LNCH-016: Download JSON export
# ---------------------------------------------------------------------------


def test_export_button_contains_valid_parseable_json(tmp_path):
    session_id, result = _wasteful_result(tmp_path)
    button = _export_button_html(session_id, result)
    assert 'class="export"' in button
    assert "data:application/json" in button

    start = button.index('href="') + len('href="')
    end = button.index('"', start)
    href = button[start:end]
    assert href.startswith("data:application/json;charset=utf-8;base64,")
    b64_payload = href.split(",", 1)[1]
    decoded = base64.b64decode(b64_payload).decode("utf-8")
    parsed = json.loads(decoded)  # must not raise
    assert parsed["kit_estimate"]["waste_tokens"] == result["kit_estimate"]["waste_tokens"]
    assert parsed["actual"]["total_tokens"] == result["actual"]["total_tokens"]


def test_render_html_includes_download_json_button(tmp_path):
    session_id, result = _wasteful_result(tmp_path)
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}
    out = render_html(session_id, meta, result, poll_seconds=1.0)
    assert 'class="export"' in out
    assert "Download JSON" in out
    assert "data:application/json" in out


# ---------------------------------------------------------------------------
# LNCH-017: empty / error states
# ---------------------------------------------------------------------------


def test_no_turns_state_shown_in_terminal_and_html():
    result = shadow_session([])
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "running"}
    terminal = render_terminal("empty-session", meta, result)
    assert NO_TURNS_MARKER in terminal
    assert "contextos-auditor doctor" in terminal
    # Must not fall through to the zeroed-table rendering.
    assert "kit estimate: waste_tokens=" not in terminal

    html_out = render_html("empty-session", meta, result, poll_seconds=1.0)
    assert NO_TURNS_MARKER in html_out
    assert "contextos-auditor doctor" in html_out


def test_zero_waste_state_is_a_success_message_not_a_zero_table(tmp_path):
    session_id, result = _clean_result(tmp_path)
    assert result["kit_estimate"]["waste_tokens"] == 0
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}

    terminal = render_terminal(session_id, meta, result)
    assert NO_WASTE_MARKER in terminal
    assert "kit estimate: waste_tokens=" not in terminal
    # No upsell CTA on a genuinely clean session (pre-existing gate).
    assert "contextos-optimiser" not in terminal

    html_out = render_html(session_id, meta, result, poll_seconds=1.0)
    assert NO_WASTE_MARKER in html_out
    assert 'class="cta"' not in html_out
    assert 'class="state-panel success"' in html_out


def test_error_status_surfaced_visibly(tmp_path):
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-4o", task="t", out_dir=tmp_path,
    )
    session.record_tool("read_file", {"path": "x.py"}, "x = 1\n")
    session.record_llm({"prompt_tokens": 5, "completion_tokens": 3}, "gpt-4o")
    session.finish(success=False, error="agent crashed mid-run")
    events = load_events(tmp_path / session.session_id)
    result = shadow_session(events)
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "error", "error": "agent crashed mid-run"}

    terminal = render_terminal(session.session_id, meta, result)
    assert SESSION_ERROR_MARKER in terminal
    assert "agent crashed mid-run" in terminal

    html_out = render_html(session.session_id, meta, result, poll_seconds=1.0)
    assert SESSION_ERROR_MARKER in html_out
    assert "agent crashed mid-run" in html_out
    assert 'class="error-banner"' in html_out


def test_no_error_banner_for_non_error_status(tmp_path):
    session_id, result = _clean_result(tmp_path)
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}
    html_out = render_html(session_id, meta, result, poll_seconds=1.0)
    assert SESSION_ERROR_MARKER not in html_out
    terminal = render_terminal(session_id, meta, result)
    assert SESSION_ERROR_MARKER not in terminal
