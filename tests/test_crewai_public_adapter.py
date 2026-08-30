"""Verifies the vendored crewai adapter (contextos_auditor/crewai.py) is
behaviorally correct, using the same real-event, no-network-call pattern as
the monorepo's dashboard/tests/test_crewai_adapter.py -- this is the
regression test for AUD-001's dry-run finding that the public package's
copy must stay in sync with the internal one."""

from __future__ import annotations

from datetime import datetime

import pytest

crewai = pytest.importorskip("crewai")

from crewai.events.event_bus import crewai_event_bus  # noqa: E402
from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallType  # noqa: E402
from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent  # noqa: E402

from contextos_auditor._internal.audit_emit import load_events  # noqa: E402
from contextos_auditor._internal.shadow_kit import shadow_session  # noqa: E402
from contextos_auditor.crewai import CrewAIAuditAdapter, attach  # noqa: E402


def _tool_finished(name: str, args: dict, output) -> ToolUsageFinishedEvent:
    now = datetime.now()
    return ToolUsageFinishedEvent(
        tool_name=name, tool_args=args, started_at=now, finished_at=now, output=output
    )


def _llm_completed(usage: dict, call_id: str) -> LLMCallCompletedEvent:
    return LLMCallCompletedEvent(
        messages="hi", response="ok", call_type=LLMCallType.LLM_CALL,
        model="gpt-4o", call_id=call_id, usage=usage,
    )


def test_adapter_logic_is_correct_given_ordered_events(tmp_path):
    adapter = CrewAIAuditAdapter(model_hint="gpt-4o", task="toy crew run", out_dir=tmp_path)

    body = "\n".join(f"# pad {i}" for i in range(200))
    old_content = "x = 1\n" + body + "\n"
    new_content = "x = 2\n" + body + "\n"

    adapter.on_tool_finished(None, _tool_finished("read_file", {"path": "a.py"}, old_content))
    adapter.on_llm_completed(None, _llm_completed({"prompt_tokens": 120, "completion_tokens": 30}, "call-1"))
    adapter.on_tool_finished(
        None, _tool_finished("write_file", {"path": "a.py", "content": new_content}, "wrote a.py")
    )
    adapter.on_llm_completed(None, _llm_completed({"prompt_tokens": 400, "completion_tokens": 20}, "call-2"))
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
            None, event=_llm_completed({"prompt_tokens": 50, "completion_tokens": 5}, "c1")
        )
        crewai_event_bus.flush()
        adapter.detach(success=True)

    events = load_events(tmp_path / adapter.session.session_id)
    assert len(events) == 1
    assert events[0]["usage"]["prompt_tokens"] == 50
    assert "read_file" in [t["name"] for t in events[0]["tool_calls"]]

