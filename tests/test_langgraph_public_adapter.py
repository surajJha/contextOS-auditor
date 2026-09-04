"""LNCH-008: adapter-level tests for `contextos_auditor/langgraph.py`.

Same shape as tests/test_crewai_public_adapter.py -- drive the *public*
entry point (`AuditorCallback`, the handler users pass in
`config={"callbacks": [...]}`) with realistic framework objects, then assert
on the events that actually landed on disk and on what
`shadow_kit.shadow_session` can conclude from them. Asserting on persisted
events rather than internal buffers is the point: that file is the product.

The callback objects here (LLMResult / AIMessage.usage_metadata / tool
output message) are fakes with langchain_core's documented shapes, so this
never needs langchain-core to be a test dependency -- see
tests/_framework_stubs.py.
"""

from __future__ import annotations

import warnings
from uuid import uuid4

from contextos_auditor._internal.audit_emit import load_events
from contextos_auditor._internal.base import _warned_labels
from contextos_auditor._internal.shadow_kit import shadow_session

from ._framework_stubs import ensure_langchain_core

ensure_langchain_core()

from contextos_auditor.langgraph import AuditorCallback, LangGraphAuditHandler  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_warned_labels():
    _warned_labels.clear()
    yield
    _warned_labels.clear()


class _FakeToolMessage:
    """LangGraph tool nodes hand `on_tool_end` a ToolMessage, not a str;
    the adapter is supposed to unwrap `.content`."""

    def __init__(self, content):
        self.content = content


class _FakeAIMessage:
    def __init__(self, usage_metadata=None, model_name=None):
        self.usage_metadata = usage_metadata
        self.response_metadata = {"model_name": model_name} if model_name else {}


class _FakeGeneration:
    def __init__(self, message):
        self.message = message


class _FakeLLMResult:
    """langchain_core's LLMResult: generations[batch][candidate], plus the
    legacy provider-specific llm_output dict."""

    def __init__(self, message=None, llm_output=None):
        self.generations = [[_FakeGeneration(message)]] if message is not None else []
        self.llm_output = llm_output


def _handler(tmp_path, **kw) -> AuditorCallback:
    return AuditorCallback(task="langgraph adapter check", out_dir=tmp_path, **kw)


def _run_tool(handler, name, args, output):
    """One complete tool round-trip, exactly as the callback dispatcher
    drives it: a start and an end correlated by the same run_id."""
    run_id = uuid4()
    handler.on_tool_start({"name": name}, str(args), run_id=run_id, inputs=args)
    handler.on_tool_end(_FakeToolMessage(output), run_id=run_id)


def _run_llm(handler, usage, model="gpt-4o"):
    handler.on_llm_end(
        _FakeLLMResult(_FakeAIMessage(usage_metadata=usage, model_name=model)),
        run_id=uuid4(),
    )


def test_callback_records_turns_and_tool_calls(tmp_path):
    handler = _handler(tmp_path, model_hint="gpt-4o")

    body = "\n".join(f"# pad {i}" for i in range(200))
    old_content = "x = 1\n" + body + "\n"
    new_content = "x = 2\n" + body + "\n"

    _run_tool(handler, "read_file", {"path": "a.py"}, old_content)
    _run_llm(handler, {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
    _run_tool(
        handler, "write_file", {"path": "a.py", "content": new_content}, "wrote a.py"
    )
    _run_llm(handler, {"input_tokens": 400, "output_tokens": 20, "total_tokens": 420})
    handler.finish(success=True)

    events = load_events(tmp_path / handler.session.session_id)
    assert len(events) == 2
    assert events[0]["tool_calls"][0]["name"] == "read_file"
    assert events[1]["tool_calls"][0]["name"] == "write_file"
    assert events[0]["usage"] == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
    }

    # The whole point of recording args/results: the estimator can replay
    # the read -> rewrite pair and price the full-file rewrite.
    out = shadow_session(events)
    assert out["kit_estimate"]["write_waste_tokens"] > 0
    assert out["save_pct"] > 0


def test_tool_args_and_results_reach_the_persisted_event(tmp_path):
    handler = _handler(tmp_path)
    _run_tool(handler, "grep", {"pattern": "TODO", "path": "src/"}, "3 matches")
    _run_llm(handler, {"input_tokens": 10, "output_tokens": 1})
    handler.finish(success=True)

    call = load_events(tmp_path / handler.session.session_id)[0]["tool_calls"][0]
    assert call["args"] == {"pattern": "TODO", "path": "src/"}
    assert call["result_text"] == "3 matches"
    assert call["chars"] == len("3 matches")


def test_input_str_is_used_when_the_dispatcher_supplies_no_inputs(tmp_path):
    """Older langchain_core versions call on_tool_start without `inputs`."""
    handler = _handler(tmp_path)
    run_id = uuid4()
    handler.on_tool_start({"name": "search"}, "weather in berlin", run_id=run_id)
    handler.on_tool_end("sunny", run_id=run_id)
    _run_llm(handler, {"input_tokens": 5, "output_tokens": 1})
    handler.finish(success=True)

    call = load_events(tmp_path / handler.session.session_id)[0]["tool_calls"][0]
    assert call["args"] == {"input": "weather in berlin"}
    assert call["result_text"] == "sunny"


def test_usage_metadata_is_preferred_and_model_name_is_backfilled(tmp_path):
    handler = _handler(tmp_path)  # no model_hint -> starts out "unknown"
    _run_llm(handler, {"input_tokens": 7, "output_tokens": 3}, model="claude-3-5-sonnet")
    handler.finish(success=True)

    event = load_events(tmp_path / handler.session.session_id)[0]
    assert event["usage"]["prompt_tokens"] == 7
    assert event["usage"]["completion_tokens"] == 3
    # total_tokens absent from UsageMetadata -> derived, never invented.
    assert event["usage"]["total_tokens"] == 10
    assert handler.session._session.model == "claude-3-5-sonnet"


def test_legacy_llm_output_token_usage_is_used_when_usage_metadata_absent(tmp_path):
    handler = _handler(tmp_path)
    handler.on_llm_end(
        _FakeLLMResult(
            _FakeAIMessage(usage_metadata=None),
            llm_output={
                "token_usage": {"prompt_tokens": 42, "completion_tokens": 8},
                "model_name": "gpt-4o-mini",
            },
        ),
        run_id=uuid4(),
    )
    handler.finish(success=True)

    event = load_events(tmp_path / handler.session.session_id)[0]
    assert event["usage"]["prompt_tokens"] == 42
    assert event["usage"]["completion_tokens"] == 8
    assert handler.session._session.model == "gpt-4o-mini"


@pytest.mark.parametrize(
    "response",
    [
        _FakeLLMResult(_FakeAIMessage(usage_metadata=None)),
        _FakeLLMResult(_FakeAIMessage(usage_metadata={})),
        _FakeLLMResult(_FakeAIMessage(usage_metadata={"input_tokens": None, "output_tokens": None})),
        _FakeLLMResult(message=None),  # no generations at all
        _FakeLLMResult(message=None, llm_output={"token_usage": None}),
        _FakeLLMResult(_FakeAIMessage(usage_metadata="not-a-dict")),
        _FakeLLMResult(message=None, llm_output=["not", "a", "dict"]),
    ],
    ids=[
        "none",
        "empty",
        "null-fields",
        "no-generations",
        "null-token-usage",
        "wrong-type",
        "non-dict-llm-output",
    ],
)
def test_missing_or_malformed_usage_records_zeros_and_never_invents_numbers(
    tmp_path, response
):
    """A trustworthy auditor reports "we saw nothing" as zero. Guessing a
    token count from a shape we don't recognise would be worse than the
    gap it papers over."""
    handler = _handler(tmp_path)
    handler.on_llm_end(response, run_id=uuid4())
    handler.finish(success=True)

    events = load_events(tmp_path / handler.session.session_id)
    assert len(events) == 1
    assert events[0]["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_a_malformed_usage_shape_does_not_lose_the_turn_or_its_tool_calls(tmp_path):
    """LNCH-008 regression: `_extract_usage` used to let a ValueError from
    an unrecognised usage_metadata escape into `on_llm_end`'s guard, so the
    whole turn was dropped and its buffered tool calls silently slid onto
    the following turn -- a wrong trace, not merely an incomplete one."""
    handler = _handler(tmp_path)

    _run_tool(handler, "read_file", {"path": "a.py"}, "x = 1\n")
    handler.on_llm_end(
        _FakeLLMResult(_FakeAIMessage(usage_metadata="not-a-dict")), run_id=uuid4()
    )
    _run_tool(handler, "grep", {"pattern": "x"}, "1 match")
    _run_llm(handler, {"input_tokens": 9, "output_tokens": 2})
    handler.finish(success=True)

    events = load_events(tmp_path / handler.session.session_id)
    assert [e["turn"] for e in events] == [1, 2]
    assert [t["name"] for t in events[0]["tool_calls"]] == ["read_file"]
    assert [t["name"] for t in events[1]["tool_calls"]] == ["grep"]
    assert events[0]["usage"]["total_tokens"] == 0
    assert events[1]["usage"]["prompt_tokens"] == 9


def test_tool_end_without_a_matching_start_is_dropped_not_guessed(tmp_path):
    handler = _handler(tmp_path)
    handler.on_tool_end("orphan output", run_id=uuid4())
    _run_llm(handler, {"input_tokens": 5, "output_tokens": 1})
    handler.finish(success=True)

    assert load_events(tmp_path / handler.session.session_id)[0]["tool_calls"] == []


def test_tools_left_pending_at_finish_are_still_flushed(tmp_path):
    """A graph that ends on a tool call (no trailing LLM turn) must not
    silently lose that call."""
    handler = _handler(tmp_path)
    _run_tool(handler, "read_file", {"path": "a.py"}, "x = 1\n")
    handler.finish(success=True)

    events = load_events(tmp_path / handler.session.session_id)
    assert len(events) == 1
    assert [t["name"] for t in events[0]["tool_calls"]] == ["read_file"]


def test_tool_session_proxy_suppresses_the_double_record(tmp_path):
    """`tool_session` exists so a self-reporting tool and the callback
    don't both log the same call; regression guard for the duplicated
    tool_calls list described in the adapter's `_ToolReportSuppressed`."""
    handler = _handler(tmp_path)
    proxy = handler.tool_session

    proxy.record_tool("read_file", {"path": "a.py"}, "x = 1\n")  # self-report: dropped
    _run_tool(handler, "read_file", {"path": "a.py"}, "x = 1\n")  # callback: kept
    _run_llm(handler, {"input_tokens": 5, "output_tokens": 1})
    handler.finish(success=True)

    events = load_events(tmp_path / handler.session.session_id)
    assert [t["name"] for t in events[0]["tool_calls"]] == ["read_file"]
    # Guard state must still be the real session's, not a copy.
    assert proxy.dedupe_guard is handler.session.dedupe_guard
    assert proxy.session_id == handler.session.session_id


def test_an_internal_auditor_failure_never_breaks_the_graph_run(tmp_path):
    """AUD-009 for LangGraph: the handler runs inline on the dispatcher's
    thread during `graph.invoke`, so an exception escaping it would
    propagate straight into the user's run. Simulate a broken recorder and
    assert the real invoke result comes back untouched."""
    handler = _handler(tmp_path)

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated internal auditor failure")

    handler.session.record_tool = _boom
    handler.session.record_llm = _boom

    def fake_graph_invoke():
        """Stands in for langchain_core's callback dispatch: every handler
        call happens between the real work and the real return value."""
        run_id = uuid4()
        handler.on_tool_start({"name": "read_file"}, "{}", run_id=run_id, inputs={"path": "a.py"})
        handler.on_tool_end(_FakeToolMessage("x = 1\n"), run_id=run_id)
        handler.on_llm_end(
            _FakeLLMResult(_FakeAIMessage({"input_tokens": 1, "output_tokens": 1})),
            run_id=run_id,
        )
        return {"messages": ["the real answer"]}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = fake_graph_invoke()

    assert result == {"messages": ["the real answer"]}
    assert any("langgraph.on_tool_end" in str(w.message) for w in caught)
    assert any("langgraph.on_llm_end" in str(w.message) for w in caught)


def test_back_compat_alias_is_the_public_class():
    assert LangGraphAuditHandler is AuditorCallback
