"""AUD-014: `watch`/`report` only ever looked at the single
most-recently-modified session under `--audit-root` -- no way to see a
history/trend across past runs. `contextos-auditor history` lists every
session found there, newest first, with basic aggregate stats, reading
only the JSONL/JSON each session already writes (no new files, no
database)."""

from __future__ import annotations

import argparse

from contextos_auditor._internal.audit_emit import AuditSession
from contextos_auditor.cli import cmd_history


def _make_session(tmp_path, session_id: str, *, prompt: int, completion: int):
    s = AuditSession(tmp_path, session_id=session_id, model="gpt-5-mini", framework="crewai", task="t")
    s.emit_turn(
        1,
        usage={"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion},
        cost={"total_nano_aiu": 0, "estimated_usd": 0.001},
        tool_calls=[],
    )
    s.finish(success=True)
    return s


def _args(tmp_path, **overrides):
    ns = argparse.Namespace(audit_root=str(tmp_path), limit=20)
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def test_history_lists_no_sessions_gracefully(tmp_path, capsys):
    rc = cmd_history(_args(tmp_path))
    assert rc == 0
    assert "No sessions found" in capsys.readouterr().out


def test_history_lists_every_session_newest_first(tmp_path, capsys):
    _make_session(tmp_path, "crewai-1000", prompt=10, completion=5)
    _make_session(tmp_path, "crewai-2000", prompt=20, completion=10)

    rc = cmd_history(_args(tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "crewai-1000" in out
    assert "crewai-2000" in out
    # newest (highest mtime, written last) appears first
    assert out.index("crewai-2000") < out.index("crewai-1000")
    assert "gpt-5-mini" in out
    assert "finished" in out


def test_history_respects_limit(tmp_path, capsys):
    for i in range(5):
        _make_session(tmp_path, f"crewai-{i}", prompt=1, completion=1)
    rc = cmd_history(_args(tmp_path, limit=2))
    assert rc == 0
    out = capsys.readouterr().out
    assert "showing 2 most recent of 5" in out


def test_history_limit_zero_means_no_limit(tmp_path, capsys):
    for i in range(3):
        _make_session(tmp_path, f"crewai-{i}", prompt=1, completion=1)
    rc = cmd_history(_args(tmp_path, limit=0))
    assert rc == 0
    out = capsys.readouterr().out
    for i in range(3):
        assert f"crewai-{i}" in out
    assert "showing" not in out
