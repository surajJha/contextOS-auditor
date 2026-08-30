"""AUD-017: `watch`/`watch --serve` are commonly started *before* the agent
run (open the dashboard, then kick off the agent), which is at least as
natural as the reverse order documented in the README. Previously, starting
either with no session yet on disk printed an error and exited immediately
-- a broken first-impression for a brand-new user who opens the dashboard
first. This test proves the fix: the plain (non---once) `watch` command now
polls until a session appears, instead of failing.

Also covers the companion fix: once a session reaches a terminal status
(finished/error), the poll loop now stops on its own printing a final
frame, instead of refreshing forever on a dead session until Ctrl-C.
"""

from __future__ import annotations

import argparse
import threading
import time

from contextos_auditor._internal.audit_emit import AuditSession
from contextos_auditor.cli import cmd_watch


def _args(tmp_path, **overrides):
    ns = argparse.Namespace(
        audit_root=str(tmp_path),
        session=None,
        session_id=None,
        poll_interval=0.05,
        html=None,
        serve=False,
        port=8765,
        once=False,
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def _write_finished_session(tmp_path):
    session = AuditSession(
        tmp_path, session_id="wait-test", model="gpt-4o-mini", task="t", framework="crewai"
    )
    session.emit_turn(
        1,
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        cost={"total_nano_aiu": 0},
        tool_calls=[],
    )
    session.finish(success=True)


def test_once_fails_fast_when_no_session_exists(tmp_path):
    """--once is the scriptable/CI-facing mode -- it must still fail
    immediately rather than block, since a CI job with no session yet is a
    real error, not something to wait out."""
    start = time.monotonic()
    rc = cmd_watch(_args(tmp_path, once=True))
    elapsed = time.monotonic() - start
    assert rc == 1
    assert elapsed < 1.0


def test_plain_watch_waits_for_a_session_to_appear(tmp_path, capsys):
    """The interactive (non---once) path must poll for a session instead of
    exiting -- this is the core AUD-017 fix."""

    def _write_soon():
        time.sleep(0.3)
        _write_finished_session(tmp_path)

    threading.Thread(target=_write_soon, daemon=True).start()

    rc = cmd_watch(_args(tmp_path, poll_interval=0.05))

    assert rc == 0
    out = capsys.readouterr().out
    assert "waiting for one to start" in out
    assert "Session found: wait-test" in out
    assert "wait-test" in out  # the actual rendered snapshot


def test_plain_watch_stops_on_its_own_once_session_finishes(tmp_path, capsys):
    """A finished session must not poll forever until Ctrl-C."""
    _write_finished_session(tmp_path)

    start = time.monotonic()
    rc = cmd_watch(_args(tmp_path, poll_interval=0.05))
    elapsed = time.monotonic() - start

    assert rc == 0
    assert elapsed < 2.0  # would hang until Ctrl-C before the fix
    out = capsys.readouterr().out
    assert "Session finished -- stopping" in out
