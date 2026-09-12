"""AUD-017: the auditor's real finding (a genuine waste_tokens>0 session)
is supposed to be the sales pitch for the paid `contextos-optimiser`
package -- architecture.md's "coupled by design" note -- but until now
nothing rendered anywhere ever pointed a user at it. This tests that a
session with real waste shows a CTA (terminal + HTML), and a clean
session with nothing to sell shows none."""

from __future__ import annotations

from contextos_auditor._internal.audit_emit import load_events
from contextos_auditor._internal.base import FrameworkAuditSession
from contextos_auditor._internal.shadow_kit import shadow_session
from contextos_auditor.report import OPTIMISER_CONTACT_EMAIL, render_html, render_terminal


def _wasteful_result(tmp_path):
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-4o", task="t", out_dir=tmp_path,
    )
    lines = ["def total(x):", "    return x", ""] * 400
    old = "\n".join(lines)
    new_lines = list(lines)
    new_lines[0] = "def total(x, y=1):"
    new = "\n".join(new_lines)
    session.record_tool("read_file", {"path": "billing.py"}, old)
    session.record_llm({"prompt_tokens": 1200, "completion_tokens": 220}, "gpt-4o")
    session.record_tool("write_file", {"path": "billing.py", "content": new}, "wrote")
    session.record_llm({"prompt_tokens": 3000, "completion_tokens": 220}, "gpt-4o")
    session.finish(success=True)
    events = load_events(tmp_path / session.session_id)
    return session.session_id, shadow_session(events)


def _clean_result(tmp_path):
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-4o", task="t", out_dir=tmp_path,
    )
    session.record_tool("read_file", {"path": "small.py"}, "x = 1\n")
    session.record_llm({"prompt_tokens": 5, "completion_tokens": 3}, "gpt-4o")
    session.finish(success=True)
    events = load_events(tmp_path / session.session_id)
    return session.session_id, shadow_session(events)


def test_terminal_shows_cta_when_real_waste_found(tmp_path):
    session_id, result = _wasteful_result(tmp_path)
    assert result["kit_estimate"]["write_waste_tokens"] > 0
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}
    out = render_terminal(session_id, meta, result)
    assert "contextos-optimiser" in out
    assert OPTIMISER_CONTACT_EMAIL in out
    assert "Estimated opportunity:" in out and "Not measured savings." in out
    assert "eliminates exactly" not in out


def test_html_shows_cta_when_real_waste_found(tmp_path):
    session_id, result = _wasteful_result(tmp_path)
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}
    out = render_html(session_id, meta, result, poll_seconds=1.0)
    assert 'class="cta"' in out
    assert "contextos-optimiser" in out
    assert f'href="mailto:{OPTIMISER_CONTACT_EMAIL}"' in out
    assert "Estimated opportunity:" in out and "Not measured savings." in out
    assert "eliminates exactly" not in out


def test_no_cta_when_session_has_no_waste(tmp_path):
    session_id, result = _clean_result(tmp_path)
    assert result["kit_estimate"]["write_waste_tokens"] == 0
    meta = {"framework": "crewai", "model": "gpt-4o", "status": "finished"}
    terminal = render_terminal(session_id, meta, result)
    assert "contextos-optimiser" not in terminal
    html_out = render_html(session_id, meta, result, poll_seconds=1.0)
    assert 'class="cta"' not in html_out
