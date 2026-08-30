"""AUD-018: live-tested with a real CrewAI crew (agent-demo/) -- both
`watch --once` and the dashboard showed `model=unknown` for the entire
run even though the LLM was built with a concrete model name. Root cause:
`FrameworkAuditSession.record_llm` receives the real model name from
CrewAI's `LLMCallCompletedEvent.model` on every call but discarded it --
the session's `model` field was only ever set once at construction time
from `model_hint` (which defaults to None -> "unknown"). This proves the
backfill fix in `_internal/base.py`."""

from __future__ import annotations

from datetime import datetime

import pytest

crewai = pytest.importorskip("crewai")

from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallType  # noqa: E402

from contextos_auditor.crewai import CrewAIAuditAdapter  # noqa: E402


def _llm_completed(usage: dict, call_id: str, model: str) -> LLMCallCompletedEvent:
    return LLMCallCompletedEvent(
        messages="hi", response="ok", call_type=LLMCallType.LLM_CALL,
        model=model, call_id=call_id, usage=usage,
    )


def test_model_backfills_from_first_real_llm_event_when_no_hint_given(tmp_path):
    # No model_hint -- this is the common case: a real user's crew.py builds
    # its own LLM object and never tells the Auditor what model that is.
    adapter = CrewAIAuditAdapter(task="toy crew run", out_dir=tmp_path)
    assert adapter.session._session.model == "unknown"

    adapter.on_llm_completed(
        None, _llm_completed({"prompt_tokens": 10, "completion_tokens": 5}, "call-1", "gpt-5-mini")
    )
    adapter.session.finish(success=True)

    session_json = (tmp_path / adapter.session.session_id / "session.json")
    import json

    meta = json.loads(session_json.read_text())
    assert meta["model"] == "gpt-5-mini"
