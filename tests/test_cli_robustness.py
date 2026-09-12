"""Regression tests for the CLI/server robustness bugs found in the 0.1.1
launch audit: torn JSONL lines (F-002/C5), locale-encoded HTML output
(F-001), `doctor` dying on a broken SDK import (C6), negative `--limit`
(C9), out-of-range `--port` (C10), `--html` silently ignored with
`--serve` (C11), a vanishing/unreadable session (C14) and the missing
`Host` header check that allowed DNS rebinding (C12).

Every test here asserts the *exact* case that was proven to fail.
"""

from __future__ import annotations

import argparse
import errno
import json
import threading
import urllib.error
import urllib.request

import pytest

from contextos_auditor._internal.audit_emit import (
    AuditSession,
    load_events,
    load_events_with_stats,
)
from contextos_auditor.cli import (
    _all_session_dirs,
    build_parser,
    cmd_doctor,
    cmd_history,
    cmd_report,
    find_running_session,
    main,
    snapshot,
)
from contextos_auditor.report import render_html, render_terminal
from contextos_auditor.server import _host_header_allowed, _make_handler


def _make_session(tmp_path, session_id="crewai-1", *, turns=3):
    s = AuditSession(
        tmp_path, session_id=session_id, model="gpt-5-mini", framework="crewai", task="t"
    )
    for i in range(turns):
        s.emit_turn(
            i + 1,
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            cost={"total_nano_aiu": 0, "estimated_usd": 0.001},
            tool_calls=[],
        )
    s.finish(success=True)
    return tmp_path / session_id


def test_empty_history_reports_html_write_failure(tmp_path, capsys):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file", encoding="utf-8")
    assert main(["history", "--audit-root", str(tmp_path / "empty"),
                 "--html", str(blocked / "history.html")]) == 1
    assert "Could not write" in capsys.readouterr().out


@pytest.mark.parametrize("interval", ["0", "-1", "nan", "inf", "-inf", "oops"])
def test_watch_rejects_invalid_poll_intervals(interval):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["watch", f"--poll-interval={interval}"])
    assert error.value.code == 2


def _truncate_last_line(session_dir):
    """Reproduces the proven failure: an agent killed mid-write leaves a
    partially-serialised final JSON object in events.jsonl."""
    path = session_dir / "events.jsonl"
    path.write_text(
        path.read_text(encoding="utf-8") + '{"turn": 4, "usage": {"prompt_to',
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# BUG-F-002 / C5 -- one torn JSONL line must not hide the whole session
# --------------------------------------------------------------------------


def test_torn_final_jsonl_line_does_not_hide_the_good_turns(tmp_path):
    session_dir = _make_session(tmp_path, turns=3)
    _truncate_last_line(session_dir)

    events, skipped = load_events_with_stats(session_dir)
    assert len(events) == 3
    assert skipped == 1
    # legacy signature still works for every existing caller
    with pytest.warns(UserWarning, match="audit totals are incomplete"):
        assert load_events(session_dir) == events


def test_torn_jsonl_is_reported_not_silently_swallowed_in_terminal_output(tmp_path):
    session_dir = _make_session(tmp_path, turns=3)
    _truncate_last_line(session_dir)

    session_id, meta, result = snapshot(session_dir)
    assert meta["unreadable_lines"] == 1
    out = render_terminal(session_id, meta, result)
    assert "1 unreadable lines skipped" in out
    assert "incomplete" in out


def test_torn_jsonl_is_reported_in_the_html_dashboard(tmp_path):
    session_dir = _make_session(tmp_path, turns=3)
    _truncate_last_line(session_dir)
    session_id, meta, result = snapshot(session_dir)
    page = render_html(session_id, meta, result, 5.0)
    assert "1 unreadable lines skipped" in page


def test_report_on_a_torn_session_exits_zero_without_a_traceback(tmp_path, capsys):
    session_dir = _make_session(tmp_path, turns=3)
    _truncate_last_line(session_dir)
    rc = main(["report", session_dir.name, "--audit-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "turns so far: 3" in out
    assert "unreadable lines skipped" in out


def test_serve_handler_still_answers_http_when_the_jsonl_is_torn(tmp_path):
    from contextos_auditor.server import _LoopbackHTTPServer

    session_dir = _make_session(tmp_path, turns=3)
    _truncate_last_line(session_dir)
    httpd = _LoopbackHTTPServer(
        ("127.0.0.1", 0), _make_handler(session_dir, 0.2, snapshot)
    )
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.status == 200
            body = resp.read().decode("utf-8")
        assert "unreadable lines skipped" in body
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------
# BUG-F-001 -- HTML must always be written as UTF-8, never the locale codec
# --------------------------------------------------------------------------


def test_html_dashboard_is_written_as_utf8_even_with_emoji_and_cjk(tmp_path, capsys):
    session = AuditSession(
        tmp_path,
        session_id="crewai-unicode",
        model="gpt-5-mini",
        framework="crewai",
        task="\U0001f680 ship it",
    )
    session.emit_turn(
        1,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        cost={"total_nano_aiu": 0, "estimated_usd": 0.001},
        tool_calls=[
            {
                "name": "read_file",
                "args": {"path": "\u6587\u66f8/\u8a2d\u5b9a\u30d5\u30a1\u30a4\u30eb.md"},
                "result": "\U0001f680 \u4f60\u597d",
            }
        ],
    )
    session.finish(success=True)

    out_html = tmp_path / "out" / "dash.html"
    rc = main(
        ["report", "crewai-unicode", "--audit-root", str(tmp_path), "--html", str(out_html)]
    )
    capsys.readouterr()
    assert rc == 0
    raw = out_html.read_bytes()
    # Must decode as UTF-8 (the template hardcodes <meta charset="utf-8">).
    text = raw.decode("utf-8")
    assert "charset=utf-8" in text


def test_html_write_does_not_depend_on_the_locale_encoding(tmp_path, monkeypatch, capsys):
    """The proven Windows failure: Path.write_text() with no `encoding=`
    uses the locale codec (cp1252), which cannot encode an emoji. Emulated
    here by making any non-utf-8 write raise exactly as cp1252 would."""
    from pathlib import Path

    real_write_text = Path.write_text

    def strict_write_text(self, data, encoding=None, *a, **kw):
        if encoding is None:
            data.encode("cp1252")  # raises UnicodeEncodeError, as on Windows
        return real_write_text(self, data, *a, encoding=encoding, **kw)

    session = AuditSession(
        tmp_path, session_id="demo-emoji", model="gpt-5-mini", framework="crewai",
        task="\U0001f680",
    )
    session.emit_turn(
        1,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        cost={"total_nano_aiu": 0, "estimated_usd": 0.0},
        tool_calls=[{"name": "\U0001f680tool", "args": {}, "result": "ok"}],
    )
    session.finish(success=True)

    monkeypatch.setattr(Path, "write_text", strict_write_text)
    out_html = tmp_path / "dash.html"
    rc = main(["report", "demo-emoji", "--audit-root", str(tmp_path), "--html", str(out_html)])
    capsys.readouterr()
    assert rc == 0
    out_html.read_bytes().decode("utf-8")


def test_session_meta_is_read_as_utf8(tmp_path):
    session_dir = tmp_path / "s"
    session_dir.mkdir()
    (session_dir / "session.json").write_text(
        json.dumps({"id": "s", "model": "gpt-5-mini", "task": "\U0001f680"}, ensure_ascii=False),
        encoding="utf-8",
    )
    from contextos_auditor.cli import read_session_meta

    assert read_session_meta(session_dir)["task"] == "\U0001f680"


# --------------------------------------------------------------------------
# BUG-C6 -- doctor must survive an SDK that raises a non-ImportError
# --------------------------------------------------------------------------


def test_doctor_survives_an_sdk_that_raises_on_import(monkeypatch, capsys):
    """PROVEN failure: an installed SDK whose transitive dependency raises
    at import time (RuntimeError, not ImportError) escaped straight out of
    `doctor` -- the one command that must never be the thing that breaks."""
    real_import = __import__

    def fake_import(name, *a, **kw):
        if name == "crewai":
            raise RuntimeError("broken transitive dep")
        if name in ("langchain_core", "agents", "autogen_core"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", fake_import)
    rc = cmd_doctor(argparse.Namespace())
    monkeypatch.undo()
    out = capsys.readouterr().out

    assert rc == 0  # optional extras being broken is not doctor failing
    assert "[broken]" in out
    assert "broken transitive dep" in out
    # the remaining frameworks were still checked after the broken one
    assert "langgraph" in out and "autogen" in out


# --------------------------------------------------------------------------
# BUG-C9 -- negative --limit
# --------------------------------------------------------------------------


def test_history_rejects_negative_limit_from_the_command_line(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["history", "--limit", "-1"])
    assert exc.value.code == 2
    assert "--limit must be >= 0" in capsys.readouterr().err


def test_history_zero_limit_still_means_no_limit(tmp_path, capsys):
    for i in range(3):
        _make_session(tmp_path, f"crewai-{i}", turns=1)
    rc = cmd_history(argparse.Namespace(audit_root=str(tmp_path), limit=0))
    out = capsys.readouterr().out
    assert rc == 0
    for i in range(3):
        assert f"crewai-{i}" in out


def test_history_negative_limit_via_direct_call_is_an_error_not_zero_rows(tmp_path, capsys):
    _make_session(tmp_path, "crewai-0", turns=1)
    rc = cmd_history(argparse.Namespace(audit_root=str(tmp_path), limit=-1))
    assert rc == 2
    assert "showing 0" not in capsys.readouterr().out


# --------------------------------------------------------------------------
# BUG-C10 -- out-of-range port
# --------------------------------------------------------------------------


def test_out_of_range_port_is_a_clean_argparse_error_not_an_overflowerror(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["watch", "--port", "99999"])
    assert exc.value.code == 2
    assert "port must be between 1 and 65535" in capsys.readouterr().err


def test_serve_session_does_not_raise_overflowerror_for_a_bad_port(tmp_path, capsys):
    from contextos_auditor.server import serve_session

    session_dir = _make_session(tmp_path, "crewai-port", turns=1)
    rc = serve_session(session_dir, poll_interval=1.0, port=99999)
    assert rc == 1
    assert "Could not bind" in capsys.readouterr().out


# --------------------------------------------------------------------------
# BUG-C11 -- --html must not be silently dropped when combined with --serve
# --------------------------------------------------------------------------


def test_watch_serve_with_html_writes_the_file_before_serving(tmp_path, capsys, monkeypatch):
    session_dir = _make_session(tmp_path, "crewai-serve", turns=1)
    served = {}

    def fake_serve(sd, *, poll_interval, port):
        served["dir"] = sd
        return 0

    monkeypatch.setattr("contextos_auditor.cli.serve_session", fake_serve)
    out_html = tmp_path / "live.html"
    rc = main(
        [
            "watch", "--session", str(session_dir),
            "--audit-root", str(tmp_path), "--serve", "--html", str(out_html),
        ]
    )
    capsys.readouterr()
    assert rc == 0
    assert served["dir"] == session_dir
    assert out_html.is_file()
    assert "<html" in out_html.read_text(encoding="utf-8")


def test_demo_serve_with_html_writes_the_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        "contextos_auditor.cli.serve_session", lambda sd, *, poll_interval, port: 0
    )
    out_html = tmp_path / "demo.html"
    rc = main(["demo", "--audit-root", str(tmp_path), "--serve", "--html", str(out_html)])
    capsys.readouterr()
    assert rc == 0
    assert out_html.is_file()


# --------------------------------------------------------------------------
# BUG-C14 -- vanishing / unreadable sessions
# --------------------------------------------------------------------------


def test_find_running_session_survives_a_file_that_vanishes_before_stat(tmp_path, monkeypatch):
    _make_session(tmp_path, "crewai-a", turns=1)
    _make_session(tmp_path, "crewai-b", turns=1)

    real_stat = type(tmp_path).stat

    def flaky_stat(self, *a, **kw):
        if self.name == "events.jsonl" and self.parent.name == "crewai-a":
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(self))
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(type(tmp_path), "stat", flaky_stat)
    assert find_running_session(tmp_path).name == "crewai-b"
    assert len(_all_session_dirs(tmp_path)) == 2


def test_history_skips_an_unreadable_session_with_a_visible_note(tmp_path, capsys, monkeypatch):
    _make_session(tmp_path, "crewai-good", turns=1)
    _make_session(tmp_path, "crewai-bad", turns=1)

    import contextos_auditor.cli as cli

    real_snapshot = cli.snapshot

    def flaky_snapshot(session_dir):
        if session_dir.name == "crewai-bad":
            raise PermissionError("events.jsonl not readable")
        return real_snapshot(session_dir)

    monkeypatch.setattr(cli, "snapshot", flaky_snapshot)
    rc = cli.cmd_history(argparse.Namespace(audit_root=str(tmp_path), limit=20))
    out = capsys.readouterr().out
    assert rc == 0
    assert "crewai-good" in out
    assert "skipped unreadable session crewai-bad" in out


def test_report_on_an_unreadable_events_file_does_not_traceback(tmp_path, capsys, monkeypatch):
    session_dir = _make_session(tmp_path, "crewai-unreadable", turns=2)
    real_read_text = type(session_dir).read_text

    def flaky_read_text(self, *a, **kw):
        if self.name == "events.jsonl":
            raise PermissionError(str(self))
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(type(session_dir), "read_text", flaky_read_text)
    rc = cmd_report(
        argparse.Namespace(
            audit_root=str(tmp_path), session_id="crewai-unreadable", session=None, html=None
        )
    )
    assert rc == 0
    assert "unreadable lines skipped" in capsys.readouterr().out


# --------------------------------------------------------------------------
# BUG-C12 -- Host header check (DNS rebinding)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1:8765", True),
        ("localhost:8765", True),
        ("127.0.0.1", True),
        ("localhost", True),
        ("[::1]:8765", True),
        (None, True),  # HTTP/1.0 clients omit Host; browsers never do
        ("evil.example.com", False),
        ("evil.example.com:8765", False),
        ("127.0.0.1.nip.io:8765", False),
        ("127.0.0.1:1234", False),  # right host, someone else's port
    ],
)
def test_host_header_allowlist(host, expected):
    assert _host_header_allowed(host, 8765) is expected


def _serve_in_thread(session_dir):
    from contextos_auditor.server import _LoopbackHTTPServer

    httpd = _LoopbackHTTPServer(("127.0.0.1", 0), _make_handler(session_dir, 0.2, snapshot))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def test_dashboard_rejects_a_rebound_host_header_and_accepts_the_local_one(tmp_path):
    session_dir = _make_session(tmp_path, "crewai-host", turns=1)
    httpd, thread = _serve_in_thread(session_dir)
    port = httpd.server_address[1]
    try:
        for path in ("/", "/events"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                headers={"Host": "evil.example.com"},
            )
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(req, timeout=5)
            with exc.value as response:
                assert response.code == 403

        # the legitimate local flow still works, page and SSE stream alike
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            assert resp.status == 200
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/events", headers={"Host": f"localhost:{port}"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert resp.readline().decode("utf-8").startswith("data: ")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
