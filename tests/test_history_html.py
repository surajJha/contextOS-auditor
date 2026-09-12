"""LNCH-014: `history` as a shareable HTML page with sparklines.

`history` was stdout-only, so the one view that shows a trend *across* runs
could not be attached to a PR or read next to the per-session dashboard --
and a fixed-width terminal table is exactly the wrong medium for a trend.

The risk in adding a second renderer is that it becomes a second, more
flattering version of the truth. The terminal view carefully reports
sessions it had to skip and lines it could not read; an HTML page that
dropped those would look like a *complete* record of every run. So both
renderers are driven from one `_history_rows()` pass, and the tests below
pin the caveats into the HTML as hard as into the text.

The sparklines have their own honesty problem: a missing value is not a
zero. A session whose model has no published price reports `None` cost, and
plotting that on the floor of the chart would read as a free run.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from contextos_auditor.cli import _history_rows, cmd_history
from contextos_auditor.report import (
    HISTORY_EMPTY_MARKER,
    _sparkline_svg,
    render_history_html,
)


def _row(**over):
    row = {
        "session_id": "sess-1",
        "framework": "crewai",
        "model": "gpt-4o",
        "status": "finished",
        "turns": 3,
        "total_tokens": 1000,
        "estimated_usd": 0.01,
        "save_pct": 12.5,
        "unreadable_lines": 0,
    }
    row.update(over)
    return row


def _rows(n=4):
    """Newest-first, as `cmd_history` produces them."""
    return [
        _row(
            session_id=f"sess-{i}",
            total_tokens=1000 * (i + 1),
            estimated_usd=0.01 * (i + 1),
            save_pct=10.0 + i,
        )
        for i in range(n)
    ][::-1]


# ------------------------------------------------------------ sparklines


def test_no_line_is_drawn_through_a_single_point():
    """A trend through one point is not a trend."""
    out = _sparkline_svg([5.0], ["a"], colour="#fff", fmt=str)
    assert "<polyline" not in out
    assert "n/a" in out


def test_no_line_when_every_value_is_missing():
    out = _sparkline_svg([None, None, None], ["a", "b", "c"], colour="#fff", fmt=str)
    assert "<polyline" not in out


def test_a_missing_value_breaks_the_line_rather_than_reading_as_zero():
    """The bug this guards: plotting `None` as 0 turns an unpriced model
    into a free run."""
    out = _sparkline_svg(
        [10.0, 20.0, None, 40.0, 50.0], list("abcde"), colour="#fff", fmt=str
    )
    assert out.count("<polyline") == 2, "the gap must split the line in two"
    # 0 must not appear as a plotted value when the minimum is 10.
    ys = [float(y) for _, y in _points(out)]
    assert min(ys) >= 0.0


def _points(svg: str):
    pts = []
    for group in re.findall(r'points="([^"]+)"', svg):
        for pair in group.split():
            x, y = pair.split(",")
            pts.append((x, y))
    return pts


def test_a_flat_series_is_drawn_flat_not_divided_by_zero():
    out = _sparkline_svg([5.0, 5.0, 5.0], list("abc"), colour="#fff", fmt=str)
    assert "<polyline" in out
    assert "nan" not in out.lower()
    ys = {y for _, y in _points(out)}
    assert len(ys) == 1, "a constant series must be a horizontal line"


def test_values_are_plotted_oldest_to_newest():
    """Sparklines read left to right; `rows` arrive newest-first."""
    html = render_history_html(_rows(4), [], 0, "/tmp/x")
    first, last = re.search(
        r"spark-range\">([^<]+) &rarr;\s*([^<]+)<", html
    ).groups()
    assert first.strip() == "sess-0"
    assert last.strip() == "sess-3"


def test_extremes_span_the_full_plot_height():
    out = _sparkline_svg([0.0, 100.0], ["a", "b"], colour="#fff", fmt=str)
    ys = sorted(float(y) for _, y in _points(out))
    assert ys[0] < ys[-1], "min and max must not collapse onto one line"


# ----------------------------------------------------------- the page


def test_page_is_self_contained():
    """It must open from disk on a machine with no network."""
    html = render_history_html(_rows(), [], 0, "/tmp/x")
    assert "https://" not in html
    assert "@import" not in html
    assert "<script" not in html


def test_page_shows_every_session():
    html = render_history_html(_rows(4), [], 0, "/tmp/x")
    for i in range(4):
        assert f"sess-{i}" in html


def test_skipped_sessions_are_named_on_the_page():
    """Silently omitting a corrupt session would make the page read as a
    complete record of every run."""
    html = render_history_html(_rows(), ["bad-sess: ValueError: boom"], 0, "/tmp/x")
    assert "bad-sess" in html
    assert "ValueError" in html


def test_unreadable_lines_are_reported_on_the_page():
    html = render_history_html(_rows(), [], 7, "/tmp/x")
    assert "7 unreadable lines" in html
    assert "incomplete" in html


def test_the_affected_row_is_flagged_not_just_the_footer():
    """A total footnote does not tell you *which* run to distrust."""
    rows = [_row(session_id="torn", unreadable_lines=3), _row(session_id="clean")]
    html = render_history_html(rows, [], 3, "/tmp/x")
    # Scope to the table: the session ids also appear in the sparkline
    # labels above it, so a whole-document search would be meaningless.
    table = html.split('<table class="history">')[1].split("</table>")[0]
    cells = {
        cell.split("</td>")[0]
        for cell in table.split('<td class="mono">')[1:]
    }
    torn = [c for c in cells if c.startswith("torn")][0]
    clean = [c for c in cells if c.startswith("clean")][0]
    assert "warn" in torn
    assert "warn" not in clean


def test_mixed_models_are_called_out():
    """Comparing cost across models mixes usage changes with price
    differences; the page should say so rather than imply a clean trend."""
    rows = [_row(session_id="a", model="gpt-4o"), _row(session_id="b", model="gpt-4o-mini")]
    html = render_history_html(rows, [], 0, "/tmp/x")
    assert "more than one model" in html
    assert "gpt-4o-mini" in html


def test_single_model_history_has_no_mixed_model_note():
    html = render_history_html(_rows(3), [], 0, "/tmp/x")
    assert "more than one model" not in html


def test_unpriced_sessions_render_as_na_not_zero():
    rows = [_row(session_id="a", estimated_usd=None)]
    html = render_history_html(rows, [], 0, "/tmp/x")
    assert "n/a" in html
    assert "$0.0000" not in html


def test_empty_history_has_an_explicit_state():
    html = render_history_html([], [], 0, "/tmp/x")
    assert HISTORY_EMPTY_MARKER in html
    assert "0 session(s)" in html


def test_session_ids_are_escaped():
    """Session ids come from the filesystem, so they are attacker-adjacent
    input for anyone who opens a shared report."""
    rows = [_row(session_id="<img src=x onerror=alert(1)>")]
    html = render_history_html(rows, [], 0, "/tmp/x")
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_no_unsubstituted_template_markers():
    html = render_history_html(_rows(), ["x: E: y"], 2, "/tmp/x")
    assert not re.search(r"__[A-Z_]+__", html)


def test_disclaimer_is_present_and_not_the_per_session_one():
    html = render_history_html(_rows(), [], 0, "/tmp/x")
    assert "does not change your bill" in html
    assert "aggregated across recorded sessions" in html


# ------------------------------------------------------------- the CLI


def _args(root, **over):
    ns = argparse.Namespace(audit_root=str(root), limit=0, html=None)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _make_sessions(root: Path, n: int) -> None:
    import time

    from contextos_auditor._internal.base import FrameworkAuditSession

    for k in range(n):
        s = FrameworkAuditSession(
            framework="crewai", model="gpt-4o", task=f"t{k}", out_dir=root
        )
        for t in range(k + 1):
            s.record_tool("read_file", {"path": "a.py"}, "x" * 400)
            s.record_tool("read_file", {"path": "a.py"}, "x" * 400)
            s.record_llm({"prompt_tokens": 500 * (t + 1), "completion_tokens": 40})
        s.finish()
        # _all_session_dirs orders by mtime; keep the order deterministic.
        time.sleep(0.01)


def test_history_html_writes_a_file(tmp_path, capsys):
    _make_sessions(tmp_path / "audit", 3)
    out = tmp_path / "hist.html"
    assert cmd_history(_args(tmp_path / "audit", html=str(out))) == 0
    html = out.read_text(encoding="utf-8")
    assert "Auditor history" in html
    assert html.count("<polyline") >= 1
    assert f"Wrote {out}" in capsys.readouterr().out


def test_html_and_terminal_report_the_same_sessions(tmp_path, capsys):
    """The two renderers must not diverge -- that is the whole reason they
    share `_history_rows`."""
    audit = tmp_path / "audit"
    _make_sessions(audit, 3)
    out = tmp_path / "hist.html"
    cmd_history(_args(audit, html=str(out)))
    printed = capsys.readouterr().out
    html = out.read_text(encoding="utf-8")
    ids = [r["session_id"] for r in _history_rows(_dirs(audit))[0]]
    assert len(ids) == 3
    for session_id in ids:
        assert session_id in printed
        assert session_id in html


def _dirs(audit_root: Path):
    from contextos_auditor.cli import _all_session_dirs

    return _all_session_dirs(audit_root)


def test_terminal_output_is_unchanged_without_html(tmp_path, capsys):
    _make_sessions(tmp_path / "audit", 2)
    assert cmd_history(_args(tmp_path / "audit")) == 0
    out = capsys.readouterr().out
    assert "session" in out and "save%" in out
    assert "Wrote" not in out


def test_empty_root_still_writes_the_file(tmp_path, capsys):
    """A stale page left from a previous run would be read as current."""
    out = tmp_path / "hist.html"
    out.write_text("STALE CONTENT", encoding="utf-8")
    assert cmd_history(_args(tmp_path / "empty", html=str(out))) == 0
    assert "STALE CONTENT" not in out.read_text(encoding="utf-8")
    assert HISTORY_EMPTY_MARKER in out.read_text(encoding="utf-8")


def test_unwritable_html_path_fails_loudly_but_keeps_the_table(tmp_path, capsys):
    _make_sessions(tmp_path / "audit", 2)
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    rc = cmd_history(_args(tmp_path / "audit", html=str(blocked / "h.html")))
    out = capsys.readouterr().out
    assert rc == 1, "a failed write must not be reported as success"
    assert "Could not write" in out
    assert "save%" in out, "the terminal table already printed must survive"


def test_html_parent_directory_is_created(tmp_path):
    _make_sessions(tmp_path / "audit", 2)
    out = tmp_path / "nested" / "deeper" / "hist.html"
    assert cmd_history(_args(tmp_path / "audit", html=str(out))) == 0
    assert out.exists()


def test_negative_limit_is_still_rejected(tmp_path):
    assert cmd_history(_args(tmp_path, limit=-1)) == 2


@pytest.mark.parametrize("n", [1, 2, 5])
def test_html_renders_for_any_session_count(tmp_path, n):
    _make_sessions(tmp_path / "audit", n)
    out = tmp_path / "h.html"
    assert cmd_history(_args(tmp_path / "audit", html=str(out))) == 0
    html = out.read_text(encoding="utf-8")
    if n < 2:
        assert "Sparklines appear once" in html
    else:
        assert "<polyline" in html
