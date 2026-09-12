"""BUG-B8: constructing a session must never take down the user's agent.

`FrameworkAuditSession.__init__` is the only entry point that is not
wrapped in `@guarded`, and it touches the filesystem three times
(`Path.cwd()`, `mkdir`, the first `session.json` write). On a read-only
mount, a full disk, a deleted cwd or a locked-down container each of those
raises straight out of the user's `AuditedCrew(...)`/`AuditedGraph(...)`
line -- crashing a run that had nothing to do with observability.

The contract these tests pin: degrade to a visibly disabled session, warn
once, and let every subsequent call be a silent no-op.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

from contextos_auditor._internal import base
from contextos_auditor._internal.base import FrameworkAuditSession


def _session(**kw):
    defaults = dict(framework="langgraph", model="gpt-4o-mini", task="t")
    defaults.update(kw)
    return FrameworkAuditSession(**defaults)


@pytest.fixture()
def readonly_dir(tmp_path: Path) -> Path:
    d = tmp_path / "locked"
    d.mkdir()
    os.chmod(d, 0o500)
    yield d
    os.chmod(d, 0o700)


@pytest.mark.skipif(os.name == "nt" or getattr(os, "geteuid", lambda: -1)() == 0,
                    reason="POSIX permission bits require a non-root POSIX user")
def test_b8_readonly_output_dir_disables_auditing_instead_of_raising(readonly_dir):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        s = _session(out_dir=readonly_dir / "audit")

    assert getattr(s._session, "disabled", False) is True
    msgs = [str(w.message) for w in caught]
    assert any("Auditing is DISABLED" in m for m in msgs), msgs
    # The user must be able to tell *why* and what to do about it.
    assert any("out_dir=" in m for m in msgs), msgs


@pytest.mark.skipif(os.name == "nt" or getattr(os, "geteuid", lambda: -1)() == 0,
                    reason="POSIX permission bits require a non-root POSIX user")
def test_b8_disabled_session_absorbs_every_subsequent_call(readonly_dir):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        s = _session(out_dir=readonly_dir / "audit")

    # None of these may raise, and none may write anything.
    s.record_tool("read_file", {"path": "a.py"}, "body")
    s.record_llm({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, "gpt-4o")
    s.finish(success=True)

    assert isinstance(s.session_id, str) and s.session_id
    assert list(readonly_dir.iterdir()) == []


def test_b8_unusable_cwd_does_not_raise(tmp_path, monkeypatch):
    """`Path.cwd()` raises FileNotFoundError if the process' working
    directory was deleted underneath it -- common with pytest tmpdirs and
    with agents that `os.chdir` into scratch space."""

    def boom():
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(base.Path, "cwd", staticmethod(boom))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        s = _session()  # no out_dir -> forced down the Path.cwd() branch

    assert getattr(s._session, "disabled", False) is True
    assert any("Auditing is DISABLED" in str(w.message) for w in caught)
    s.finish(success=True)


def test_b8_warning_names_the_underlying_cause(tmp_path, monkeypatch):
    """A bare "auditing disabled" is not actionable; the original exception
    type and message must survive into the warning."""

    def boom(*a, **k):
        raise PermissionError(13, "Read-only file system")

    monkeypatch.setattr(base, "AuditSession", boom)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _session(out_dir=tmp_path / "audit")

    msg = "\n".join(str(w.message) for w in caught)
    assert "PermissionError" in msg
    assert "Read-only file system" in msg


def test_b8_warning_is_emitted_only_once_per_process(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(base, "AuditSession", boom)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for _ in range(5):
            _session(out_dir=tmp_path / "audit")

    disabled = [w for w in caught if "Auditing is DISABLED" in str(w.message)]
    assert len(disabled) == 1, f"expected one warning, got {len(disabled)}"


def test_b8_a_broken_otel_endpoint_does_not_block_startup(tmp_path, monkeypatch):
    """OTel export is an optional extra; a failure to build the exporter
    must not stop the session that would have recorded locally anyway."""

    def boom(*a, **k):
        raise RuntimeError("collector unreachable")

    monkeypatch.setattr(base, "build_exporter", boom)
    s = _session(out_dir=tmp_path / "audit", otel_endpoint="http://127.0.0.1:1/v1/traces")

    assert s._otel is None
    assert getattr(s._session, "disabled", False) is False  # local recording survives
    s.record_llm({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, "gpt-4o")
    s.finish(success=True)
    assert (s._session.dir / "events.jsonl").exists()


def test_b8_a_writable_directory_is_completely_unaffected(tmp_path):
    """Guard against the fix silently disabling the happy path."""
    s = _session(out_dir=tmp_path / "audit")
    assert getattr(s._session, "disabled", False) is False
    s.record_llm({"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}, "gpt-4o")
    s.finish(success=True)
    assert (s._session.dir / "events.jsonl").read_text(encoding="utf-8").strip()


def test_same_millisecond_sessions_never_share_capture_files(tmp_path, monkeypatch):
    from contextos_auditor._internal.audit_emit import load_events

    monkeypatch.setattr(base.time, "time", lambda: 1700000000.123)
    first = _session(out_dir=tmp_path, task="first user")
    second = _session(out_dir=tmp_path, task="second user")
    first.record_llm({"prompt_tokens": 11, "completion_tokens": 1}, "gpt-4o")
    second.record_llm({"prompt_tokens": 22, "completion_tokens": 2}, "gpt-4o")
    first.finish(success=True)
    second.finish(success=True)
    assert first.session_id != second.session_id
    assert len(list(tmp_path.glob("*/session.json"))) == 2
    assert load_events(first._session.dir)[0]["usage"]["prompt_tokens"] == 11
    assert load_events(second._session.dir)[0]["usage"]["prompt_tokens"] == 22


def test_capture_notice_does_not_deny_opt_in_export(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CONTEXTOS_QUIET", raising=False)
    _session(out_dir=tmp_path)
    notice = capsys.readouterr().err
    assert "unless OpenTelemetry export is enabled" in notice
    assert "nothing leaves your machine" not in notice
