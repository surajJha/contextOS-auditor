"""Free Auditor adapter for the OpenAI Agents SDK.

The SDK's tracing system already emits every LLM call and tool call as a
`Span` on a global `TraceProvider`: a `TracingProcessor` (`agents.tracing`)
receives `on_span_end(span)` for both `GenerationSpanData` (usage: input/
output tokens, model) and `FunctionSpanData` (name/input/output — the SDK's
built-in and `@function_tool`-decorated tools alike, plus `LocalShellTool`
and `ApplyPatchTool` results). Attaching costs nothing extra in the user's
agent code — `add_trace_processor` is a global registration:

    from contextos_auditor.openai_agents import attach
    audit = attach(model_hint="gpt-4o-mini", task="...")
    result = await Runner.run(agent, "do the thing")
    audit.detach()

Requires: `pip install contextos-auditor[openai-agents]`
"""

from __future__ import annotations

import json
from typing import Any

from contextos_auditor._internal.base import FrameworkAuditSession, guarded
from contextos_auditor._internal.compat import check_compat


def attach(
    *,
    model_hint: str | None = None,
    task: str = "openai-agents run",
    out_dir=None,
    arm: str = "baseline",
    session_id: str | None = None,
    otel_endpoint: str | None = None,
) -> "OpenAIAgentsAuditAdapter":
    check_compat("openai_agents")
    adapter = OpenAIAgentsAuditAdapter(
        model_hint=model_hint, task=task, out_dir=out_dir, arm=arm,
        session_id=session_id, otel_endpoint=otel_endpoint,
    )
    adapter.attach()
    return adapter


class OpenAIAgentsAuditAdapter:
    def __init__(
        self,
        *,
        model_hint: str | None = None,
        task: str = "openai-agents run",
        out_dir=None,
        arm: str = "baseline",
        session_id: str | None = None,
        otel_endpoint: str | None = None,
    ) -> None:
        # AUDIT-008: see crewai_adapter's identical comment -- arm/session_id
        # were previously unreachable through the public attach function.
        self.session = FrameworkAuditSession(
            framework="openai-agents-sdk",
            model=model_hint,
            task=task,
            out_dir=out_dir,
            arm=arm,
            session_id=session_id,
            otel_endpoint=otel_endpoint,
        )
        self._processor: Any = None

    @guarded("openai_agents.on_span_end")
    def on_span_end(self, span: Any) -> None:
        """Bound method (named, not a closure) so tests can drive it
        directly with a fake span object without a real Runner.run()."""
        data = getattr(span, "span_data", None)
        span_type = getattr(data, "type", None)
        if span_type == "generation":
            usage = getattr(data, "usage", None) or {}
            self.session.record_llm(usage, getattr(data, "model", None))
        elif span_type == "function":
            name = getattr(data, "name", "tool")
            raw_input = getattr(data, "input", None)
            args: Any = raw_input
            if isinstance(raw_input, str):
                try:
                    args = json.loads(raw_input)
                except (ValueError, TypeError):
                    args = {"input": raw_input}
            output = getattr(data, "output", None)
            self.session.record_tool(name, args, output)

    def attach(self) -> None:
        from agents.tracing import TracingProcessor, add_trace_processor

        outer = self

        class _AuditorProcessor(TracingProcessor):
            def on_trace_start(self, trace: Any) -> None:
                pass

            def on_trace_end(self, trace: Any) -> None:
                pass

            def on_span_start(self, span: Any) -> None:
                pass

            def on_span_end(self, span: Any) -> None:
                outer.on_span_end(span)

            def shutdown(self) -> None:
                pass

            def force_flush(self) -> None:
                pass

        self._processor = _AuditorProcessor()
        add_trace_processor(self._processor)

    def detach(self, *, success: bool | None = None, error: str = "") -> None:
        # The SDK only exposes add_trace_processor / set_trace_processors
        # (replace-all) publicly. Best-effort remove just ours from the
        # global provider's internal tuple rather than nuking the user's
        # other processors (e.g. the default OpenAI export processor).
        try:
            from agents.tracing.setup import get_trace_provider

            multi = get_trace_provider()._multi_processor
            with multi._lock:
                multi._processors = tuple(
                    p for p in multi._processors if p is not self._processor
                )
        except Exception:
            pass
        self.session.finish(success=success, error=error)


# Back-compat alias matching the monorepo's internal name.
attach_openai_agents_auditor = attach
