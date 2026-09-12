"""LNCH-008: adapter-level tests for `contextos_auditor/openai_agents.py`.

Same shape as tests/test_crewai_public_adapter.py: drive the public entry
point with objects that have the SDK's real span shapes and assert on the
events written to disk, plus what `shadow_kit.shadow_session` can conclude
from them.

The OpenAI Agents SDK is not a test dependency (see
tests/_framework_stubs.py for why). `on_span_end` is a plain bound method
precisely so a fake span can drive it without a Runner; the one place that
genuinely needs the SDK -- `attach()`/`detach()`, which register on the
global trace provider -- is exercised against a fake `agents.tracing`
module that mimics the provider's multi-processor registry, so the
registration/de-registration wiring is still covered rather than skipped.
"""

from __future__ import annotations

import json
import sys
import threading
import types
import warnings
from types import SimpleNamespace

import pytest

from contextos_auditor._internal.audit_emit import load_events
from contextos_auditor._internal.base import _warned_labels
from contextos_auditor._internal.shadow_kit import shadow_session
from contextos_auditor.openai_agents import (
    OpenAIAgentsAuditAdapter,
    attach,
    attach_openai_agents_auditor,
)


@pytest.fixture(autouse=True)
def _reset_warned_labels():
    _warned_labels.clear()
    yield
    _warned_labels.clear()


class _SpanData:
    """`GenerationSpanData` / `FunctionSpanData` are plain attribute bags
    distinguished by their `type` string; the adapter reads them with
    getattr only, so this stands in for either."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Span:
    def __init__(self, data):
        self.span_data = data


def _generation(usage, model="gpt-4o"):
    return _Span(_SpanData(type="generation", usage=usage, model=model, input=[], output=[]))


def _function(name, raw_input, output):
    # The SDK serialises tool arguments to a JSON string before they reach
    # the span, which is why the adapter parses `input` rather than trusting
    # it to already be a dict.
    return _Span(_SpanData(type="function", name=name, input=raw_input, output=output))


def _adapter(tmp_path, **kw) -> OpenAIAgentsAuditAdapter:
    return OpenAIAgentsAuditAdapter(task="openai-agents adapter check", out_dir=tmp_path, **kw)


def test_span_processing_records_turns_and_tool_calls(tmp_path):
    adapter = _adapter(tmp_path, model_hint="gpt-4o")

    body = "\n".join(f"# pad {i}" for i in range(200))
    old_content = "x = 1\n" + body + "\n"
    new_content = "x = 2\n" + body + "\n"

    adapter.on_span_end(_function("read_file", json.dumps({"path": "a.py"}), old_content))
    adapter.on_span_end(_generation({"input_tokens": 120, "output_tokens": 30}))
    adapter.on_span_end(
        _function(
            "write_file",
            json.dumps({"path": "a.py", "content": new_content}),
            "wrote a.py",
        )
    )
    adapter.on_span_end(_generation({"input_tokens": 400, "output_tokens": 20}))
    adapter.detach(success=True)

    events = load_events(tmp_path / adapter.session.session_id)
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


@pytest.mark.parametrize("kind", ["turn", "task"])
def test_aggregate_usage_spans_do_not_warn_or_double_count(tmp_path, kind):
    adapter = _adapter(tmp_path)
    adapter.on_span_end(_generation({"input_tokens": 10, "output_tokens": 3}))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        adapter.on_span_end(_Span(_SpanData(type=kind, usage={"input_tokens": 10, "output_tokens": 3})))
    assert not caught
    events = load_events(tmp_path / adapter.session.session_id)
    assert len(events) == 1
    assert events[0]["usage"]["total_tokens"] == 13


def test_tool_input_json_is_parsed_so_the_estimator_can_read_path_and_content(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.on_span_end(
        _function("write_file", json.dumps({"path": "b.py", "content": "y = 2\n"}), "ok")
    )
    adapter.on_span_end(_generation({"input_tokens": 10, "output_tokens": 1}))
    adapter.detach(success=True)

    call = load_events(tmp_path / adapter.session.session_id)[0]["tool_calls"][0]
    assert call["args"] == {"path": "b.py", "content": "y = 2\n"}
    assert call["result_text"] == "ok"


def test_non_json_tool_input_is_preserved_verbatim(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.on_span_end(_function("search", "weather in berlin", "sunny"))
    adapter.on_span_end(_generation({"input_tokens": 5, "output_tokens": 1}))
    adapter.detach(success=True)

    call = load_events(tmp_path / adapter.session.session_id)[0]["tool_calls"][0]
    assert call["args"] == {"input": "weather in berlin"}
    assert call["result_text"] == "sunny"


def test_generation_span_backfills_the_real_model_name(tmp_path):
    adapter = _adapter(tmp_path)  # no model_hint -> "unknown" until a span says otherwise
    adapter.on_span_end(_generation({"input_tokens": 3, "output_tokens": 4}, model="gpt-4o-mini"))
    adapter.detach(success=True)

    assert adapter.session._session.model == "gpt-4o-mini"
    event = load_events(tmp_path / adapter.session.session_id)[0]
    assert event["usage"]["prompt_tokens"] == 3
    assert event["usage"]["completion_tokens"] == 4
    # total_tokens absent from the span usage -> derived, not invented.
    assert event["usage"]["total_tokens"] == 7


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": None, "output_tokens": None},
        {"prompt_tokens": 0, "completion_tokens": 0},
        {"unexpected_field": 99},
    ],
    ids=["missing", "empty", "null-fields", "zeros", "unknown-keys"],
)
def test_missing_or_unexpected_usage_records_zeros_and_never_invents_numbers(tmp_path, usage):
    adapter = _adapter(tmp_path)
    adapter.on_span_end(_generation(usage))
    adapter.detach(success=True)

    events = load_events(tmp_path / adapter.session.session_id)
    assert len(events) == 1
    assert events[0]["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def test_span_without_usage_attribute_at_all_is_still_counted_as_a_turn(tmp_path):
    """SDK drift: a generation span that no longer carries `usage` must
    still produce a turn, otherwise the turn count silently shrinks."""
    adapter = _adapter(tmp_path)
    adapter.on_span_end(_Span(_SpanData(type="generation", model="gpt-4o")))
    adapter.detach(success=True)

    events = load_events(tmp_path / adapter.session.session_id)
    assert len(events) == 1
    assert events[0]["usage"]["total_tokens"] == 0


def test_unrelated_span_types_are_ignored(tmp_path):
    """Agent/handoff/guardrail spans carry no usage and no tool call; they
    must not create phantom turns.

    BUG-A1: "response" was removed from this list -- it is the SDK's
    default LLM span type and is now deliberately recorded as a turn (see
    `test_response_span_*`), so listing it here would re-encode the
    zero-tokens bug."""
    adapter = _adapter(tmp_path)
    for span_type in ("agent", "handoff", "guardrail", None):
        adapter.on_span_end(_Span(_SpanData(type=span_type, name="x")))
    adapter.on_span_end(_Span(None))  # span with no span_data at all
    adapter.detach(success=True)

    assert load_events(tmp_path / adapter.session.session_id) == []


def _fake_agents_tracing(monkeypatch):
    """Mimics `agents.tracing`: a global provider holding a tuple of
    processors behind a lock, plus `add_trace_processor`."""

    class TracingProcessor:
        pass

    class _MultiProcessor:
        def __init__(self):
            self._lock = threading.Lock()
            self._processors: tuple = ()

    class _Provider:
        def __init__(self):
            self._multi_processor = _MultiProcessor()

    provider = _Provider()

    def add_trace_processor(processor):
        with provider._multi_processor._lock:
            provider._multi_processor._processors += (processor,)

    agents_mod = types.ModuleType("agents")
    agents_mod.__path__ = []
    tracing_mod = types.ModuleType("agents.tracing")
    tracing_mod.TracingProcessor = TracingProcessor
    tracing_mod.add_trace_processor = add_trace_processor
    setup_mod = types.ModuleType("agents.tracing.setup")
    setup_mod.get_trace_provider = lambda: provider
    tracing_mod.setup = setup_mod
    agents_mod.tracing = tracing_mod

    monkeypatch.setitem(sys.modules, "agents", agents_mod)
    monkeypatch.setitem(sys.modules, "agents.tracing", tracing_mod)
    monkeypatch.setitem(sys.modules, "agents.tracing.setup", setup_mod)
    return provider


def test_attach_registers_a_processor_that_routes_real_spans(tmp_path, monkeypatch):
    provider = _fake_agents_tracing(monkeypatch)
    audit = attach(model_hint="gpt-4o", task="attach check", out_dir=tmp_path)

    assert audit._processor in provider._multi_processor._processors

    # Drive the span through the *registered* processor, the way the SDK
    # does, rather than through the adapter method directly.
    for processor in provider._multi_processor._processors:
        processor.on_span_end(_function("read_file", json.dumps({"path": "a.py"}), "x = 1\n"))
        processor.on_span_end(_generation({"input_tokens": 50, "output_tokens": 5}))
    audit.detach(success=True)

    events = load_events(tmp_path / audit.session.session_id)
    assert len(events) == 1
    assert events[0]["usage"]["prompt_tokens"] == 50
    assert [t["name"] for t in events[0]["tool_calls"]] == ["read_file"]


def test_detach_removes_only_our_processor(tmp_path, monkeypatch):
    """`set_trace_processors` would nuke the user's own exporters; detach
    must surgically drop just ours."""
    provider = _fake_agents_tracing(monkeypatch)
    someone_elses = object()
    provider._multi_processor._processors = (someone_elses,)

    audit = attach(task="detach check", out_dir=tmp_path)
    assert len(provider._multi_processor._processors) == 2

    audit.detach(success=True)
    assert provider._multi_processor._processors == (someone_elses,)


def test_finished_adapter_cannot_reattach_to_global_provider(tmp_path, monkeypatch):
    provider = _fake_agents_tracing(monkeypatch)
    audit = attach(task="reattach finished", out_dir=tmp_path)
    audit.detach(success=True)
    audit.attach()
    assert provider._multi_processor._processors == ()
    audit.on_span_end(_generation({"input_tokens": 999, "output_tokens": 1}))
    assert load_events(tmp_path / audit.session.session_id) == []


def test_repeated_detach_finishes_only_once(tmp_path, monkeypatch):
    _fake_agents_tracing(monkeypatch)
    audit = attach(task="repeat detach", out_dir=tmp_path)
    finishes = []
    monkeypatch.setattr(audit.session, "finish", lambda **kwargs: finishes.append(kwargs))
    audit.detach(success=True)
    audit.detach()
    assert finishes == [{"success": True, "error": ""}]


def test_attach_writes_to_project_local_contextos_dir_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _fake_agents_tracing(monkeypatch)
    audit = attach(model_hint="gpt-4o", task="default out_dir check")
    audit.detach(success=True)

    expected = tmp_path / ".contextos" / "audit" / audit.session.session_id
    assert expected.is_dir()
    assert (expected / "session.json").is_file()


def test_an_internal_auditor_failure_never_breaks_the_agent_run(tmp_path, monkeypatch):
    """AUD-009 for the OpenAI Agents SDK: the SDK calls `on_span_end`
    inline while finishing a span inside `Runner.run`. If our recording
    raises there, the user loses a run that already completed and was
    already billed."""
    provider = _fake_agents_tracing(monkeypatch)
    audit = attach(task="crash isolation", out_dir=tmp_path)

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated internal auditor failure")

    audit.session.record_llm = _boom
    audit.session.record_tool = _boom

    def fake_runner_run():
        """Stands in for `Runner.run`: spans end (dispatching to every
        registered processor) before the real result is returned."""
        for processor in provider._multi_processor._processors:
            processor.on_span_end(_function("read_file", "{}", "x = 1\n"))
            processor.on_span_end(_generation({"input_tokens": 1, "output_tokens": 1}))
        return "the real final output"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = fake_runner_run()

    assert result == "the real final output"
    assert any("openai_agents.on_span_end" in str(w.message) for w in caught)
    audit.detach(success=True)


def test_back_compat_alias_is_the_public_attach():
    assert attach_openai_agents_auditor is attach


def test_a_failing_unregistration_warns_and_still_finishes_the_session(
    tmp_path, monkeypatch
):
    """BUG-A9: `detach()` reaches into SDK privates and warns if that fails --
    but the warning helper was never imported into this module, so the
    `except` handler raised `NameError` instead of warning.

    `detach()` is deliberately NOT `@guarded` (it is the caller's explicit
    'the run ended' signal, not an SDK callback), so that NameError escaped
    into the user's own code *and* skipped `session.finish()` below it,
    leaving the session permanently unfinished. Both halves are asserted
    here: the run survives, and the session is still closed out.
    """
    provider = _fake_agents_tracing(monkeypatch)
    audit = attach(task="detach failure", out_dir=tmp_path)

    class _ExplodingLock:
        def __enter__(self):
            raise RuntimeError("SDK internals moved")

        def __exit__(self, *_a):
            return False

    provider._multi_processor._lock = _ExplodingLock()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        audit.detach(success=True)

    assert any("openai_agents.detach" in str(w.message) for w in caught), (
        "the unregistration failure must surface as a warning, not a NameError"
    )

    meta = json.loads((tmp_path / audit.session.session_id / "session.json").read_text())
    assert meta["status"] == "finished"


def test_an_unhandled_span_carrying_usage_warns_about_under_counting(tmp_path):
    """BUG-A9, second call site: a span type the adapter does not handle but
    which reports usage means the SDK grew a new LLM shape and this audit is
    now silently under-counting tokens. That warning is an honesty mechanism,
    and it was unreachable -- `_warn_once` raised `NameError`, which
    `@guarded` then relabelled as a generic internal error, so the specific
    'your token counts are incomplete' signal never reached anyone.
    """
    audit = OpenAIAgentsAuditAdapter(task="unhandled span", out_dir=tmp_path)

    span = SimpleNamespace(
        span_data=SimpleNamespace(
            type="some_future_span", usage={"input_tokens": 10, "output_tokens": 2}
        )
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        audit.on_span_end(span)

    messages = [str(w.message) for w in caught]
    assert any("unhandled_span_type" in m for m in messages), messages
    assert not any("NameError" in m for m in messages), messages
