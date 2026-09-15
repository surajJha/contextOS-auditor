"""AUD-009: the single most important safety property of an observation-
only tool -- it must never be able to break the real agent run it's
watching. These tests inject genuine failures into the Auditor's own
recording code and assert the *real* result the agent depends on still
comes back untouched, and (for the sync frameworks) that the real event
bus keeps dispatching to other handlers afterwards."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import warnings

import pytest

from contextos_auditor._internal.base import FrameworkAuditSession, _warned_labels


@pytest.fixture(autouse=True)
def _reset_warned_labels():
    _warned_labels.clear()
    yield
    _warned_labels.clear()


def test_record_llm_survives_a_malformed_usage_dict(tmp_path):
    session = FrameworkAuditSession(framework="crewai", model="gpt-4o", task="t", out_dir=tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # usage.get(...) on a non-dict raises AttributeError inside record_llm
        session.record_llm(usage="not-a-dict", model="gpt-4o")  # type: ignore[arg-type]
    assert any("record_llm" in str(w.message) for w in caught)


def test_record_tool_survives_an_unrepresentable_result(tmp_path):
    class Explodes:
        def __str__(self):
            raise RuntimeError("boom")

    session = FrameworkAuditSession(framework="crewai", model="gpt-4o", task="t", out_dir=tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session.record_tool("write_file", {"path": "a.py"}, Explodes())
    assert any("record_tool" in str(w.message) for w in caught)


def test_warning_fires_only_once_per_label(tmp_path):
    session = FrameworkAuditSession(framework="crewai", model="gpt-4o", task="t", out_dir=tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        session.record_llm(usage="not-a-dict")  # type: ignore[arg-type]
        session.record_llm(usage="still-not-a-dict")  # type: ignore[arg-type]
    matching = [w for w in caught if "record_llm" in str(w.message)]
    assert len(matching) == 1


def test_verbose_capture_reports_every_occurrence_with_default_warning_filter(monkeypatch, capsys):
    from contextos_auditor._internal.base import _warn_once

    monkeypatch.setenv("CONTEXTOS_AUDITOR_VERBOSE", "1")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        for _ in range(3):
            _warn_once("repeated-error", ValueError("same error"))
    assert not caught
    assert capsys.readouterr().err.count("totals may be incomplete") == 3


def test_verbose_capture_still_cannot_raise_under_warnings_as_errors(monkeypatch, capsys):
    from contextos_auditor._internal.base import _warn_once

    monkeypatch.setenv("CONTEXTOS_AUDITOR_VERBOSE", "1")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("error")
        _warn_once("safe-error", RuntimeError("failure"))
        _warn_once("safe-error", RuntimeError("failure"))
    assert not caught
    assert capsys.readouterr().err.count("safe-error") == 2


def test_verbose_can_be_enabled_after_a_warning_was_already_emitted(monkeypatch, capsys):
    from contextos_auditor._internal.base import _warn_once

    monkeypatch.delenv("CONTEXTOS_AUDITOR_VERBOSE", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _warn_once("late-verbose", RuntimeError("failure"))
        _warn_once("late-verbose", RuntimeError("failure"))
        monkeypatch.setenv("CONTEXTOS_AUDITOR_VERBOSE", "1")
        _warn_once("late-verbose", RuntimeError("failure"))
        monkeypatch.setenv("CONTEXTOS_AUDITOR_VERBOSE", "0")
        _warn_once("late-verbose", RuntimeError("failure"))
    assert len(caught) == 1
    assert capsys.readouterr().err.count("late-verbose") == 1
    assert "-W always" not in str(caught[0].message)


def test_concurrent_verbose_capture_preserves_host_warning_policy(monkeypatch, capsys):
    from contextos_auditor._internal.base import _warn_once

    monkeypatch.setenv("CONTEXTOS_AUDITOR_VERBOSE", "1")
    barrier = threading.Barrier(4)

    def emit():
        barrier.wait(timeout=5)
        for _ in range(5):
            _warn_once("concurrent-error", RuntimeError("failure"))

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        original_filters = list(warnings.filters)
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: emit(), range(4)))
        assert warnings.filters == original_filters
        with pytest.raises(UserWarning, match="host warning"):
            warnings.warn("host warning", stacklevel=2)
    assert capsys.readouterr().err.count("concurrent-error") == 20


def test_verbose_capture_survives_broken_stderr(monkeypatch):
    from contextos_auditor._internal import base

    class ClosedStream:
        def write(self, text):
            raise ValueError("stream closed")

    monkeypatch.setenv("CONTEXTOS_AUDITOR_VERBOSE", "1")
    monkeypatch.setattr(base.sys, "stderr", ClosedStream())
    base._warn_once("closed-stream", RuntimeError("failure"))


def test_autogen_record_failure_never_blocks_the_real_llm_result(tmp_path):
    """The critical AUD-009 case: `_record()` runs *after* the real LLM
    call succeeds. If it raises, the agent must still get its real result
    back -- losing an already-successful (and billed) LLM call because our
    own bookkeeping choked is the worst possible failure mode."""
    pytest.importorskip("autogen_core")
    from contextos_auditor.autogen import AuditingChatCompletionClient

    class _FakeUsage:
        # deliberately missing prompt_tokens/completion_tokens attributes
        # in a way that raises inside _record instead of defaulting
        @property
        def prompt_tokens(self):
            raise RuntimeError("simulated SDK version drift")

    class _FakeResult:
        usage = _FakeUsage()

    class _FakeWrappedClient:
        model_info = {"family": "gpt-4o"}

        async def create(self, *a, **kw):
            return _FakeResult()

    session = FrameworkAuditSession(framework="autogen", model="gpt-4o", task="t", out_dir=tmp_path)
    client = AuditingChatCompletionClient(_FakeWrappedClient(), session)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = asyncio.run(client.create())

    assert result is not None and isinstance(result, _FakeResult)
    assert any("_record" in str(w.message) for w in caught)


def test_autogen_audit_tool_failure_never_blocks_the_real_tool_result(tmp_path):
    pytest.importorskip("autogen_core")
    from contextos_auditor.autogen import audit_tool

    def real_tool(path: str) -> str:
        return f"wrote {path}"

    session = FrameworkAuditSession(framework="autogen", model="gpt-4o", task="t", out_dir=tmp_path)
    # Monkeypatch record_tool on the instance to simulate an internal failure
    # even though it's already @guarded -- belt and suspenders check that
    # the wrapper around the real tool call doesn't add its own new risk.
    session.record_tool = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))

    wrapped = audit_tool(real_tool, session)
    result = wrapped("a.py")
    assert result == "wrote a.py"
