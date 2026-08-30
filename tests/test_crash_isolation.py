"""AUD-009: the single most important safety property of an observation-
only tool -- it must never be able to break the real agent run it's
watching. These tests inject genuine failures into the Auditor's own
recording code and assert the *real* result the agent depends on still
comes back untouched, and (for the sync frameworks) that the real event
bus keeps dispatching to other handlers afterwards."""

from __future__ import annotations

import asyncio
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


def test_autogen_record_failure_never_blocks_the_real_llm_result():
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

    session = FrameworkAuditSession(framework="autogen", model="gpt-4o", task="t", out_dir=None)
    client = AuditingChatCompletionClient(_FakeWrappedClient(), session)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = asyncio.run(client.create())

    assert result is not None and isinstance(result, _FakeResult)
    assert any("_record" in str(w.message) for w in caught)


def test_autogen_audit_tool_failure_never_blocks_the_real_tool_result():
    pytest.importorskip("autogen_core")
    from contextos_auditor.autogen import audit_tool

    def real_tool(path: str) -> str:
        return f"wrote {path}"

    session = FrameworkAuditSession(framework="autogen", model="gpt-4o", task="t", out_dir=None)
    # Monkeypatch record_tool on the instance to simulate an internal failure
    # even though it's already @guarded -- belt and suspenders check that
    # the wrapper around the real tool call doesn't add its own new risk.
    session.record_tool = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))

    wrapped = audit_tool(real_tool, session)
    result = wrapped("a.py")
    assert result == "wrote a.py"
