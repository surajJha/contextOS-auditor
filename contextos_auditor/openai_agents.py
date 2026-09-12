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

from contextos_auditor._internal.base import FrameworkAuditSession, _warn_once, guarded
from contextos_auditor._internal.compat import check_compat


def attach(
    *,
    model_hint: str | None = None,
    task: str = "openai-agents run",
    out_dir=None,
    arm: str = "baseline",
    session_id: str | None = None,
    otel_endpoint: str | None = None,
    redact_secrets: bool | None = None,
) -> "OpenAIAgentsAuditAdapter":
    check_compat("openai_agents")
    adapter = OpenAIAgentsAuditAdapter(
        model_hint=model_hint, task=task, out_dir=out_dir, arm=arm,
        session_id=session_id, otel_endpoint=otel_endpoint, redact_secrets=redact_secrets,
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
        redact_secrets: bool | None = None,
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
            otel_endpoint=otel_endpoint, redact_secrets=redact_secrets,
        )
        self._processor: Any = None
        # BUG-A5: initialised here, not only in attach(), so on_span_end is
        # safe on an adapter that was constructed but never attached (the
        # guard would otherwise raise AttributeError and drop the span).
        self._detached = False

    @guarded("openai_agents.on_span_end")
    def on_span_end(self, span: Any) -> None:
        """Bound method (named, not a closure) so tests can drive it
        directly with a fake span object without a real Runner.run()."""
        # BUG-A5: a failed `detach()` used to leave this processor registered
        # on the global provider, so the *next* run's spans were appended to
        # an already-finished session. Refuse to record once detached, no
        # matter what the SDK's internals did with our registration.
        if self._detached:
            return
        data = getattr(span, "span_data", None)
        span_type = getattr(data, "type", None)
        if span_type == "generation":
            usage = getattr(data, "usage", None) or {}
            self.session.record_llm(usage, getattr(data, "model", None))
        elif span_type == "response":
            # BUG-A1: this is the SDK's DEFAULT path. `Runner.run()` with
            # `OpenAIResponsesModel` emits a ResponseSpanData (type
            # "response"), not a GenerationSpanData -- only the legacy
            # ChatCompletions model emits "generation". Ignoring it meant a
            # default agent run recorded zero LLM turns and the dashboard
            # confidently reported "0 tokens used", with no warning at all.
            # Verified against openai-agents `tracing/span_data.py` (usage
            # dict with input_tokens/output_tokens/total_tokens, set from
            # `model_usage_to_span_usage`) and `models/openai_responses.py`.
            usage = getattr(data, "usage", None) or {}
            response = getattr(data, "response", None)
            model = getattr(response, "model", None)
            self.session.record_llm(usage, model)
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
        elif span_type in ("turn", "task"):
            # SDK 0.19+ adds aggregate usage spans around the same model calls.
            # Their usage is already counted by response/generation spans.
            return
        elif getattr(data, "usage", None):
            # An unrecognised span type that nonetheless carries usage means
            # the SDK grew a new LLM span shape and we are now under-counting.
            # Stay loud rather than silently reporting a smaller bill.
            _warn_once(
                f"openai_agents.unhandled_span_type({span_type!r})",
                RuntimeError(
                    "span carries usage but is not handled; token counts for it "
                    "are missing from this audit"
                ),
            )

    def attach(self) -> None:
        from agents.tracing import TracingProcessor, add_trace_processor

        # BUG-A4: `attach()` used to register unconditionally while keeping
        # only the last processor in `self._processor`. A second call (a
        # retry after an exception, a re-run notebook cell) registered a
        # second processor on the *global* provider, so every span was
        # recorded twice into the same session -- every number doubled --
        # and `detach()` could only ever remove one of them.
        if self._processor is not None or self._detached:
            return

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
        self._detached = False
        add_trace_processor(self._processor)

    def detach(self, *, success: bool | None = None, error: str = "") -> None:
        if self._detached:
            return
        # The SDK only exposes add_trace_processor / set_trace_processors
        # (replace-all) publicly. Best-effort remove just ours from the
        # global provider's internal tuple rather than nuking the user's
        # other processors (e.g. the default OpenAI export processor).
        #
        # BUG-A5: this reaches into SDK privates, so it WILL break on some
        # future version. It used to fail with `except Exception: pass` --
        # no warning, and the still-registered processor then wrote the next
        # run's spans into this finished session. Set `_detached` first so
        # correctness never depends on the unregistration succeeding, and
        # warn if it does fail so the drift is discoverable.
        self._detached = True
        try:
            from agents.tracing.setup import get_trace_provider

            multi = get_trace_provider()._multi_processor
            with multi._lock:
                multi._processors = tuple(
                    p for p in multi._processors if p is not self._processor
                )
        except Exception as exc:  # noqa: BLE001 -- must never break the user's run
            _warn_once("openai_agents.detach", exc)
        self._processor = None
        self.session.finish(success=success, error=error)


# Back-compat alias matching the monorepo's internal name.
attach_openai_agents_auditor = attach
