"""Verifies the vendored crewai adapter (contextos_auditor/crewai.py).

BUG-BB001: this file previously began with a module-level
`pytest.importorskip("crewai")`. Because `crewai` is an optional extra and
is not installed in CI or on a bare `pip install pytest` machine, the entire
file skipped and `crewai.py` sat at **0% executed coverage** while the suite
reported green -- for the one adapter whose measured savings number (43.4%)
is quoted on the marketing site. The adapter only needs the crewai SDK
inside `attach()`/`detach()`, never at import time, so a stand-in event bus
(see `tests/_framework_stubs.ensure_crewai`) exercises the real adapter code
on any machine. Where the real SDK *is* installed, the stub steps aside and
these tests run against genuine crewai objects.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from enum import Enum

import pytest

from ._framework_stubs import ensure_crewai

ensure_crewai()

from crewai.events.event_bus import crewai_event_bus  # noqa: E402
from crewai.events.types.llm_events import LLMCallCompletedEvent  # noqa: E402
from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent  # noqa: E402

from contextos_auditor._internal.audit_emit import load_events  # noqa: E402
from contextos_auditor._internal.shadow_kit import shadow_session  # noqa: E402
from contextos_auditor.crewai import CrewAIAuditAdapter, attach  # noqa: E402


def _build(model_cls, **kwargs):
    """Construct a crewai event, filling in fields the SDK requires but the
    adapter never reads.

    Version drift: crewai made `started_at`/`finished_at` required on
    `ToolUsageFinishedEvent`, and `response`/`call_type` required on
    `LLMCallCompletedEvent`, after these tests were written. The adapter only
    ever *reads* attributes off events crewai itself constructs, so the drift
    cannot reach shipped code -- but a test that can no longer build the SDK's
    own event stops exercising the adapter entirely, which is how this file
    went red without a single adapter change. Discover the required set from
    the model rather than hardcoding it, so the next field crewai adds does
    not break the suite again.
    """
    for name, field in getattr(model_cls, "model_fields", {}).items():
        if name in kwargs or not field.is_required():
            continue
        ann = field.annotation
        if isinstance(ann, type) and issubclass(ann, datetime):
            kwargs[name] = datetime.now(timezone.utc)
        elif isinstance(ann, type) and issubclass(ann, Enum):
            kwargs[name] = next(iter(ann))
        else:
            kwargs[name] = None
    return model_cls(**kwargs)


def _tool_finished(name: str, args: dict, output) -> ToolUsageFinishedEvent:
    return _build(ToolUsageFinishedEvent, tool_name=name, tool_args=args, output=output)


def _llm_completed(usage, call_id: str = "c1") -> LLMCallCompletedEvent:
    return _build(LLMCallCompletedEvent, usage=usage, model="gpt-4o", call_id=call_id)


def _tool_finished_unvalidated(name, args: dict, output) -> ToolUsageFinishedEvent:
    """Build an event carrying a value crewai's own schema would now reject.

    BUG-B2 is specifically about frameworks handing the adapter a tool name
    that is not a `str`. crewai has since tightened `tool_name` to `str`, so
    its validating constructor can no longer express the adversarial input --
    but other frameworks still emit enums and tool objects, and the adapter
    must survive them. Use pydantic's non-validating constructor so the test
    keeps asserting the adapter's tolerance rather than crewai's strictness.
    """
    construct = getattr(ToolUsageFinishedEvent, "model_construct", None)
    if construct is None:  # stand-in bus, or a pre-pydantic-v2 crewai
        return _tool_finished(name, args, output)
    return construct(tool_name=name, tool_args=args, output=output)


def test_adapter_logic_is_correct_given_ordered_events(tmp_path):
    adapter = CrewAIAuditAdapter(model_hint="gpt-4o", task="toy crew run", out_dir=tmp_path)

    body = "\n".join(f"# pad {i}" for i in range(200))
    old_content = "x = 1\n" + body + "\n"
    new_content = "x = 2\n" + body + "\n"

    adapter.on_tool_finished(None, _tool_finished("read_file", {"path": "a.py"}, old_content))
    adapter.on_llm_completed(None, _llm_completed({"prompt_tokens": 120, "completion_tokens": 30}))
    adapter.on_tool_finished(
        None, _tool_finished("write_file", {"path": "a.py", "content": new_content}, "wrote a.py")
    )
    adapter.on_llm_completed(
        None, _llm_completed({"prompt_tokens": 400, "completion_tokens": 20}, "c2")
    )
    adapter.session.finish(success=True)

    events = load_events(tmp_path / adapter.session.session_id)
    assert len(events) == 2
    assert events[0]["tool_calls"][0]["name"] == "read_file"
    assert events[1]["tool_calls"][0]["name"] == "write_file"

    out = shadow_session(events)
    assert out["kit_estimate"]["write_waste_tokens"] > 0
    assert out["save_pct"] > 0


def test_attach_writes_to_project_local_contextos_dir_by_default(tmp_path, monkeypatch):
    """AUD-001 finding: the monorepo default was `./out/audit`; the public
    package must default to `./.contextos/audit` (project-local, no
    assumption the caller is inside the toku monorepo)."""
    monkeypatch.chdir(tmp_path)
    with crewai_event_bus.scoped_handlers():
        audit = attach(model_hint="gpt-4o", task="default out_dir check")
        audit.detach(success=True)

    expected = tmp_path / ".contextos" / "audit" / audit.session.session_id
    assert expected.is_dir()
    assert (expected / "session.json").is_file()


def test_attach_and_detach_cleanly_route_real_bus_events(tmp_path):
    with crewai_event_bus.scoped_handlers():
        adapter = attach(model_hint="gpt-4o", task="bus routing check", out_dir=tmp_path)
        crewai_event_bus.emit(None, event=_tool_finished("read_file", {"path": "a.py"}, "x = 1\n"))
        crewai_event_bus.emit(
            None, event=_llm_completed({"prompt_tokens": 50, "completion_tokens": 5})
        )
        crewai_event_bus.flush()
        adapter.detach(success=True)

    events = load_events(tmp_path / adapter.session.session_id)
    assert len(events) == 1
    assert events[0]["usage"]["prompt_tokens"] == 50
    assert "read_file" in [t["name"] for t in events[0]["tool_calls"]]


def test_detach_unregisters_so_later_events_do_not_reach_a_finished_session(tmp_path):
    """`detach()` calls `crewai_event_bus.off(...)`. If that ever stops
    working, events from the *next* crew run would be appended to this
    already-finished session and silently inflate its numbers."""
    with crewai_event_bus.scoped_handlers():
        adapter = attach(model_hint="gpt-4o", task="detach check", out_dir=tmp_path)
        adapter.detach(success=True)
        crewai_event_bus.emit(
            None, event=_llm_completed({"prompt_tokens": 999, "completion_tokens": 9})
        )
        crewai_event_bus.flush()

    events = load_events(tmp_path / adapter.session.session_id)
    assert events == [], "a detached adapter must not record anything further"


def test_a_failing_off_call_still_finishes_the_session(tmp_path, monkeypatch):
    """AUD-009: one failing `off()` must not prevent `session.finish()` --
    that is the caller's real 'the run ended' signal."""
    with crewai_event_bus.scoped_handlers():
        adapter = attach(model_hint="gpt-4o", task="off failure", out_dir=tmp_path)

        def boom(event_type, handler):
            raise RuntimeError("bus drift")

        monkeypatch.setattr(crewai_event_bus, "off", boom)
        with pytest.warns(UserWarning):
            adapter.detach(success=True)
        # crewai's `scoped_handlers()` now calls `off` on context exit too, so
        # the stand-in has to be withdrawn once the call under test has been
        # made -- otherwise teardown, not the adapter, is what raises.
        monkeypatch.undo()

    meta = json.loads((tmp_path / adapter.session.session_id / "session.json").read_text())
    assert meta["status"] == "finished"


def test_malformed_usage_still_records_a_visible_turn(tmp_path):
    """BUG-A2: crewai's `LLMCallCompletedEvent.usage` is typed `dict[str,
    Any]`, i.e. unvalidated provider data. A non-numeric value used to raise
    inside `record_llm`, whose `@guarded` wrapper then swallowed the WHOLE
    turn -- losing its token counts and re-attributing its buffered tool
    calls to the following turn. Degrade to a visible zero instead."""
    adapter = CrewAIAuditAdapter(model_hint="gpt-4o", task="bad usage", out_dir=tmp_path)
    adapter.on_tool_finished(None, _tool_finished("read_file", {"path": "a.py"}, "x = 1\n"))
    adapter.on_llm_completed(None, _llm_completed({"prompt_tokens": "12", "total_tokens": "NA"}))
    adapter.on_llm_completed(
        None, _llm_completed({"prompt_tokens": 400, "completion_tokens": 20}, "c2")
    )
    adapter.session.finish(success=True)

    events = load_events(tmp_path / adapter.session.session_id)
    assert len(events) == 2, "the malformed turn must still be recorded, not dropped"
    # Its own tool call stays with it rather than sliding into turn 2.
    assert [t["name"] for t in events[0]["tool_calls"]] == ["read_file"]
    assert events[0]["usage"]["prompt_tokens"] == 12
    assert events[1]["tool_calls"] == []


def test_usage_none_is_recorded_as_a_zero_turn(tmp_path):
    adapter = CrewAIAuditAdapter(model_hint="gpt-4o", task="none usage", out_dir=tmp_path)
    adapter.on_llm_completed(None, _llm_completed(None))
    adapter.session.finish(success=True)
    events = load_events(tmp_path / adapter.session.session_id)
    assert len(events) == 1
    assert events[0]["usage"]["total_tokens"] == 0


def test_non_string_tool_name_is_still_recorded(tmp_path):
    """BUG-B2: frameworks pass enums and tool objects, not just str. These
    used to raise inside `_normalize_tool_name`, and `@guarded` then dropped
    the tool call entirely -- making a duplicate read invisible."""

    class ToolName:
        def __str__(self) -> str:
            return "read_file"

    adapter = CrewAIAuditAdapter(model_hint="gpt-4o", task="odd name", out_dir=tmp_path)
    adapter.on_tool_finished(
        None, _tool_finished_unvalidated(ToolName(), {"path": "a.py"}, "x = 1\n")
    )
    adapter.on_llm_completed(None, _llm_completed({"prompt_tokens": 10, "completion_tokens": 1}))
    adapter.session.finish(success=True)

    events = load_events(tmp_path / adapter.session.session_id)
    assert [t["name"] for t in events[0]["tool_calls"]] == ["read_file"]


def test_concurrent_bus_dispatch_never_loses_a_turn_or_a_tool_call(tmp_path):
    """BUG-A3: the real `CrewAIEventsBus` submits sync handlers to a
    ThreadPoolExecutor, so handlers genuinely run concurrently. Exact
    tool->turn attribution cannot be guaranteed under out-of-order dispatch
    (that is a property of the bus, not of us), but nothing may be *lost* or
    double-counted, and the shared buffer must not corrupt.
    """
    adapter = CrewAIAuditAdapter(model_hint="gpt-4o", task="threaded", out_dir=tmp_path)
    barrier = threading.Barrier(8)

    def fire(i: int) -> None:
        barrier.wait()
        adapter.on_tool_finished(None, _tool_finished("read_file", {"path": f"f{i}.py"}, "x"))
        adapter.on_llm_completed(None, _llm_completed({"prompt_tokens": 10, "completion_tokens": 1}))

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    adapter.session.finish(success=True)

    events = load_events(tmp_path / adapter.session.session_id)
    assert len(events) == 8, "every LLM call must produce exactly one turn"
    total_tools = sum(len(e["tool_calls"]) for e in events)
    assert total_tools == 8, "no tool call may be lost or duplicated"
    assert sum(e["usage"]["prompt_tokens"] for e in events) == 80
    assert sorted(e["turn"] for e in events) == list(range(1, 9))


def test_detach_drains_queued_observations_before_finish(tmp_path, monkeypatch):
    with crewai_event_bus.scoped_handlers():
        audit = attach(out_dir=tmp_path)
        drained = []

        def flush():
            drained.append(True)
            audit.on_llm_completed(None, _llm_completed({"prompt_tokens": 7}))
            return True

        monkeypatch.setattr(crewai_event_bus, "flush", flush)
        audit.detach(success=True)
        assert drained == [True]
        assert load_events(tmp_path / audit.session.session_id)[0]["usage"]["prompt_tokens"] == 7


def test_failed_unregister_cannot_capture_later_runs(tmp_path, monkeypatch):
    with crewai_event_bus.scoped_handlers():
        audit = attach(out_dir=tmp_path)

        def boom(*args):
            raise RuntimeError("unregister failed")

        monkeypatch.setattr(crewai_event_bus, "off", boom)
        with pytest.warns(UserWarning, match="unregister failed"):
            audit.detach(success=True)
        audit.on_llm_completed(None, _llm_completed({"prompt_tokens": 999}))
        audit.on_tool_finished(None, _tool_finished("read_file", {"path": "late"}, "late"))
        audit.attach()
        assert not audit._handlers
        assert load_events(tmp_path / audit.session.session_id) == []
        monkeypatch.undo()


def test_repeated_attach_and_detach_are_idempotent(tmp_path):
    with crewai_event_bus.scoped_handlers():
        audit = attach(out_dir=tmp_path)
        audit.attach()
        crewai_event_bus.emit(None, event=_llm_completed({"prompt_tokens": 7}))
        audit.detach(success=True)
        audit.detach()
        audit.attach()
        assert not audit._handlers
    assert len(load_events(tmp_path / audit.session.session_id)) == 1
