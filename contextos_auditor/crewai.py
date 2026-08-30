"""Free Auditor adapter for CrewAI.

CrewAI ships its own typed pub/sub event bus (a singleton,
`crewai.events.event_bus.crewai_event_bus`) with normalized usage on
`LLMCallCompletedEvent.usage` (prompt/completion/cached/reasoning/
cache_creation tokens, provider-agnostic) and tool I/O on
`ToolUsageFinishedEvent.tool_name`/`.tool_args`/`.output`.

Subscribing needs zero changes to your `Crew`/`Agent`/`Task` definitions:

    from contextos_auditor.crewai import attach
    audit = attach(task="fix the bug")
    crew.kickoff()
    audit.detach()

Requires: `pip install contextos-auditor[crewai]`
"""

from __future__ import annotations

from typing import Any

from contextos_auditor._internal.base import FrameworkAuditSession
from contextos_auditor._internal.compat import check_compat


def attach(
    *,
    model_hint: str | None = None,
    task: str = "crewai run",
    out_dir=None,
    arm: str = "baseline",
    session_id: str | None = None,
) -> "CrewAIAuditAdapter":
    """Attach the Auditor to CrewAI's global event bus. One call, no changes
    to your `Crew`/`Agent`/`Task` code. Call `.detach()` when the run ends."""
    check_compat("crewai")
    adapter = CrewAIAuditAdapter(
        model_hint=model_hint, task=task, out_dir=out_dir, arm=arm, session_id=session_id
    )
    adapter.attach()
    return adapter


# Back-compat alias matching the monorepo's internal name, in case anyone
# copies a snippet from internal docs before they're updated.
attach_crewai_auditor = attach


class CrewAIAuditAdapter:
    def __init__(
        self,
        *,
        model_hint: str | None = None,
        task: str = "crewai run",
        out_dir=None,
        arm: str = "baseline",
        session_id: str | None = None,
    ) -> None:
        self.session = FrameworkAuditSession(
            framework="crewai",
            model=model_hint,
            task=task,
            out_dir=out_dir,
            arm=arm,
            session_id=session_id,
        )
        self._handlers: list[tuple[Any, Any]] = []

    def on_llm_completed(self, source: Any, event: Any) -> None:
        self.session.record_llm(event.usage or {}, event.model)

    def on_tool_finished(self, source: Any, event: Any) -> None:
        self.session.record_tool(event.tool_name, event.tool_args, event.output)

    def attach(self) -> None:
        from crewai.events.event_bus import crewai_event_bus
        from crewai.events.types.llm_events import LLMCallCompletedEvent
        from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent

        crewai_event_bus.on(LLMCallCompletedEvent)(self.on_llm_completed)
        crewai_event_bus.on(ToolUsageFinishedEvent)(self.on_tool_finished)
        self._handlers = [
            (LLMCallCompletedEvent, self.on_llm_completed),
            (ToolUsageFinishedEvent, self.on_tool_finished),
        ]

    def detach(self, *, success: bool | None = None, error: str = "") -> None:
        from crewai.events.event_bus import crewai_event_bus

        for event_type, handler in self._handlers:
            crewai_event_bus.off(event_type, handler)
        self._handlers = []
        self.session.finish(success=success, error=error)
