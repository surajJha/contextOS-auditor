"""AUD-013: previously the dashboard/terminal report only showed session
totals -- no way to see which tool call happened in which turn, or which
specific write caused waste. This adds a nested turn -> tool-call trace to
both the terminal (`report`/`watch` without --html) and static/live HTML
render paths, built entirely from data `shadow_session()` already returns
(no new instrumentation)."""

from __future__ import annotations

from contextos_auditor._internal.audit_emit import load_events
from contextos_auditor._internal.base import FrameworkAuditSession
from contextos_auditor._internal.shadow_kit import shadow_session
from contextos_auditor.report import render_html, render_terminal


def _real_result(tmp_path):
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path,
    )
    old_yaml = "service: bot\nversion: 1\nfeatures:\n  x: false\n"
    new_yaml = "service: bot\nversion: 1\nfeatures:\n  x: true\n"
    session.record_tool("read_file", {"path": "service.yaml"}, old_yaml)
    session.record_llm({"prompt_tokens": 5, "completion_tokens": 3}, "gpt-5-mini")
    session.record_tool("write_file", {"path": "service.yaml", "content": new_yaml}, "wrote")
    session.record_llm({"prompt_tokens": 7, "completion_tokens": 4}, "gpt-5-mini")
    session.finish(success=True)
    events = load_events(tmp_path / session.session_id)
    return session.session_id, shadow_session(events)


def test_render_terminal_shows_nested_turn_and_tool_trace(tmp_path):
    session_id, result = _real_result(tmp_path)
    meta = {"framework": "crewai", "model": "gpt-5-mini", "status": "finished"}
    out = render_terminal(session_id, meta, result)
    assert "trace (turn -> tool calls):" in out
    assert "turn 1" in out
    assert "read_file" in out
    assert "turn 2" in out
    assert "write_file" in out
    assert "waste detected: write to service.yaml" in out


def test_render_html_includes_nested_trace_tree(tmp_path):
    session_id, result = _real_result(tmp_path)
    meta = {"framework": "crewai", "model": "gpt-5-mini", "status": "finished"}
    out = render_html(session_id, meta, result, poll_seconds=1.0)
    assert '<details class="trace"' in out
    assert "turn 1" in out
    assert "<li>read_file</li>" in out
    assert "<li>write_file</li>" in out
    assert 'class="waste"' in out
    assert "service.yaml" in out


def test_trace_omitted_when_no_turns(tmp_path):
    result = shadow_session([])
    meta = {"framework": "crewai", "model": "gpt-5-mini", "status": "running"}
    terminal = render_terminal("empty-session", meta, result)
    assert "trace (turn -> tool calls):" not in terminal
    html_out = render_html("empty-session", meta, result, poll_seconds=1.0)
    assert '<details class="trace"' not in html_out
