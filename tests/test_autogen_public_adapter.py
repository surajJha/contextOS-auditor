"""LNCH-008: adapter-level tests for `contextos_auditor/autogen.py`.

Same shape as tests/test_crewai_public_adapter.py -- drive the public entry
points (`new_session` / `wrap_client` / `audit_tool`) with a fake AGNext
model client and plain tool callables, then assert on the events written to
disk and on what `shadow_kit.shadow_session` derives from them.

AutoGen has no global hook, so the adapter is a client proxy plus a tool
wrapper; both sit *in* the caller's execution path, which makes the
"observation never breaks the real call" property below load-bearing rather
than theoretical. tests/test_crash_isolation.py already covers that for
`create()` and `audit_tool()`; the streaming path is covered here instead of
duplicating those.

autogen-core is not a test dependency -- see tests/_framework_stubs.py.
"""

from __future__ import annotations

import asyncio
import inspect
import warnings

import pytest

from contextos_auditor._internal.audit_emit import load_events
from contextos_auditor._internal.base import _warned_labels
from contextos_auditor._internal.shadow_kit import shadow_session

from ._framework_stubs import ensure_autogen_core

ensure_autogen_core()

from contextos_auditor.autogen import (  # noqa: E402
    AuditingChatCompletionClient,
    audit_tool,
    new_session,
    wrap_client,
)


@pytest.fixture(autouse=True)
def _reset_warned_labels():
    _warned_labels.clear()
    yield
    _warned_labels.clear()


class _FakeUsage:
    """AGNext's `RequestUsage`: prompt_tokens / completion_tokens."""

    def __init__(self, prompt_tokens=0, completion_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeCreateResult:
    def __init__(self, content="the real model output", usage=None):
        self.content = content
        self.usage = usage


class _FakeClient:
    """Stands in for `OpenAIChatCompletionClient`; every delegated method
    returns a distinct sentinel so the proxy can be checked for
    transparency."""

    def __init__(self, result=None, chunks=()):
        self.model_info = {"family": "gpt-4o", "vision": False}
        self.capabilities = {"vision": False}
        self._result = result if result is not None else _FakeCreateResult()
        self._chunks = list(chunks)
        self.closed = False
        self.create_calls: list[tuple] = []

    async def create(self, *args, **kwargs):
        self.create_calls.append((args, kwargs))
        return self._result

    async def create_stream(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk

    def actual_usage(self):
        return "actual-usage-sentinel"

    def total_usage(self):
        return "total-usage-sentinel"

    def count_tokens(self, *args, **kwargs):
        return 7

    def remaining_tokens(self, *args, **kwargs):
        return 3

    async def close(self):
        self.closed = True


def write_file(path: str, content: str) -> str:
    return f"wrote {path}"


def read_file(path: str) -> str:
    return _FILES[path]


_FILES: dict[str, str] = {}


def test_wrapped_client_and_audited_tools_record_turns_and_tool_calls(tmp_path):
    session = new_session(model="gpt-4o", task="autogen adapter check", out_dir=tmp_path)

    body = "\n".join(f"# pad {i}" for i in range(200))
    old_content = "x = 1\n" + body + "\n"
    new_content = "x = 2\n" + body + "\n"
    _FILES["a.py"] = old_content

    client = wrap_client(_FakeClient(_FakeCreateResult(usage=_FakeUsage(120, 30))), session)
    audited_read = audit_tool(read_file, session)
    audited_write = audit_tool(write_file, session)

    assert audited_read("a.py") == old_content
    asyncio.run(client.create())
    assert audited_write("a.py", new_content) == "wrote a.py"
    asyncio.run(client.create())
    session.finish(success=True)

    events = load_events(tmp_path / session.session_id)
    assert len(events) == 2
    assert events[0]["tool_calls"][0]["name"] == "read_file"
    assert events[1]["tool_calls"][0]["name"] == "write_file"
    assert events[0]["usage"] == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
    }

    out = shadow_session(events)
    assert out["kit_estimate"]["write_waste_tokens"] > 0
    assert out["save_pct"] > 0


def test_positional_tool_args_are_recorded_under_their_real_parameter_names(tmp_path):
    """shadow_kit looks up `path`/`content` by name, so a tool called
    positionally must not degrade to an unnamed tuple."""
    session = new_session(model="gpt-4o", task="arg binding", out_dir=tmp_path)
    audit_tool(write_file, session)("b.py", "y = 2\n")
    session.finish(success=True)

    call = load_events(tmp_path / session.session_id)[0]["tool_calls"][0]
    assert call["args"] == {"path": "b.py", "content": "y = 2\n"}
    assert call["result_text"] == "wrote b.py"


def test_audited_tool_keeps_the_original_signature(tmp_path):
    """AGNext builds each tool's args schema from `inspect.signature`; a
    wrapper that hid it behind (*args, **kwargs) would break every call."""
    session = new_session(task="signature", out_dir=tmp_path)
    wrapped = audit_tool(write_file, session)
    assert inspect.signature(wrapped) == inspect.signature(write_file)
    assert wrapped.__name__ == "write_file"


def test_async_tools_are_audited_and_return_their_real_result(tmp_path):
    session = new_session(task="async tool", out_dir=tmp_path)

    async def fetch(url: str) -> str:
        return f"body of {url}"

    result = asyncio.run(audit_tool(fetch, session)("https://example.com"))
    session.finish(success=True)

    assert result == "body of https://example.com"
    call = load_events(tmp_path / session.session_id)[0]["tool_calls"][0]
    assert call["name"] == "fetch"
    assert call["args"] == {"url": "https://example.com"}
    assert call["result_text"] == "body of https://example.com"


def test_create_returns_the_real_result_object_untouched(tmp_path):
    session = new_session(task="passthrough", out_dir=tmp_path)
    real = _FakeCreateResult(usage=_FakeUsage(1, 2))
    inner = _FakeClient(real)
    client = wrap_client(inner, session)

    got = asyncio.run(client.create("messages", tools=["t"]))
    session.finish(success=True)

    assert got is real
    assert inner.create_calls == [(("messages",), {"tools": ["t"]})]


def test_model_family_is_backfilled_from_the_wrapped_client(tmp_path):
    session = new_session(task="model backfill", out_dir=tmp_path)  # model stays "unknown"
    client = wrap_client(_FakeClient(_FakeCreateResult(usage=_FakeUsage(5, 5))), session)
    asyncio.run(client.create())
    session.finish(success=True)

    assert session._session.model == "gpt-4o"


@pytest.mark.parametrize(
    "usage",
    [
        None,
        _FakeUsage(None, None),
        object(),  # no prompt_tokens/completion_tokens attributes at all
        _FakeUsage(0, 0),
    ],
    ids=["missing", "null-fields", "unknown-shape", "zeros"],
)
def test_missing_or_unexpected_usage_records_zeros_and_never_invents_numbers(tmp_path, usage):
    session = new_session(task="usage variants", out_dir=tmp_path)
    client = wrap_client(_FakeClient(_FakeCreateResult(usage=usage)), session)

    result = asyncio.run(client.create())
    session.finish(success=True)

    assert isinstance(result, _FakeCreateResult)
    events = load_events(tmp_path / session.session_id)
    assert len(events) == 1
    assert events[0]["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_create_stream_records_usage_from_the_final_chunk_only(tmp_path):
    session = new_session(model="gpt-4o", task="streaming", out_dir=tmp_path)
    final = _FakeCreateResult(usage=_FakeUsage(80, 12))
    client = wrap_client(_FakeClient(chunks=["par", "tial", final]), session)

    async def drain():
        return [chunk async for chunk in client.create_stream()]

    chunks = asyncio.run(drain())
    session.finish(success=True)

    assert chunks == ["par", "tial", final]
    events = load_events(tmp_path / session.session_id)
    assert len(events) == 1
    assert events[0]["usage"]["prompt_tokens"] == 80
    assert events[0]["usage"]["completion_tokens"] == 12


def test_create_stream_without_a_usage_bearing_chunk_records_nothing(tmp_path):
    """No usage anywhere in the stream means we genuinely don't know the
    token count -- emitting a zero-token turn would misreport turn count."""
    session = new_session(task="streaming no usage", out_dir=tmp_path)
    client = wrap_client(_FakeClient(chunks=["a", "b"]), session)

    async def drain():
        return [chunk async for chunk in client.create_stream()]

    assert asyncio.run(drain()) == ["a", "b"]
    session.finish(success=True)
    assert load_events(tmp_path / session.session_id) == []


def test_an_internal_failure_never_breaks_a_streaming_run(tmp_path):
    """AUD-009 for the streaming path (test_crash_isolation.py covers the
    non-streaming `create()` and `audit_tool()` cases): every real chunk
    must still reach the caller when our own recording blows up."""
    session = new_session(task="streaming crash isolation", out_dir=tmp_path)
    final = _FakeCreateResult(usage=_FakeUsage(1, 1))
    client = wrap_client(_FakeClient(chunks=["a", final]), session)

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated internal auditor failure")

    session.record_llm = _boom

    async def drain():
        return [chunk async for chunk in client.create_stream()]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        chunks = asyncio.run(drain())

    assert chunks == ["a", final]
    assert any("autogen._record" in str(w.message) for w in caught)


def test_proxy_delegates_every_other_client_method_unchanged(tmp_path):
    session = new_session(task="proxy transparency", out_dir=tmp_path)
    inner = _FakeClient()
    client = wrap_client(inner, session)

    assert client.actual_usage() == "actual-usage-sentinel"
    assert client.total_usage() == "total-usage-sentinel"
    assert client.count_tokens("messages") == 7
    assert client.remaining_tokens("messages") == 3
    assert client.model_info is inner.model_info
    assert client.capabilities is inner.capabilities
    asyncio.run(client.close())
    assert inner.closed is True


def test_back_compat_alias_is_the_public_client_wrapper():
    assert wrap_client is AuditingChatCompletionClient


def test_stream_records_final_usage_before_returning_it(tmp_path):
    session = new_session(task="final chunk delivery", out_dir=tmp_path)
    final = _FakeCreateResult(usage=_FakeUsage(7, 2))
    client = wrap_client(_FakeClient(chunks=[final]), session)

    async def consume():
        stream = client.create_stream()
        assert await anext(stream) is final
        events = load_events(tmp_path / session.session_id)
        assert len(events) == 1
        assert events[0]["usage"]["total_tokens"] == 9
        session.finish(success=True)
        await stream.aclose()

    asyncio.run(consume())
    assert len(load_events(tmp_path / session.session_id)) == 1


def test_closing_outer_stream_closes_wrapped_stream_immediately(tmp_path):
    session = new_session(task="stream close", out_dir=tmp_path)
    closed = []

    class Client(_FakeClient):
        async def create_stream(self, *args, **kwargs):
            try:
                yield "partial"
                yield _FakeCreateResult(usage=_FakeUsage(7, 2))
            finally:
                closed.append(True)

    async def consume():
        stream = wrap_client(Client(), session).create_stream()
        assert await anext(stream) == "partial"
        await stream.aclose()
        assert closed == [True]

    asyncio.run(consume())
    session.finish(success=True)
    assert load_events(tmp_path / session.session_id) == []
