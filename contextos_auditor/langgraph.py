"""Free Auditor adapter for LangGraph / LangChain (and any langchain_core-
based custom agent, since LangGraph agents are built entirely on top of
langchain_core's Runnable + callback system).

Requires: `pip install contextos-auditor[langgraph]`

langchain_core standardizes usage across providers on `AIMessage.usage_metadata`
(a `UsageMetadata` TypedDict: input_tokens/output_tokens/total_tokens) and
tool I/O on `BaseCallbackHandler.on_tool_start`/`on_tool_end`. Passing the
handler through `config={"callbacks": [...]}` (or `graph.invoke(..., config=...)`)
needs no change to the graph/tool definitions themselves:

    from contextos_auditor.langgraph import AuditorCallback
    handler = AuditorCallback(task="...")
    graph.invoke({"messages": [...]}, config={"callbacks": [handler]})
    handler.finish()
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from contextos_auditor._internal.base import FrameworkAuditSession
from contextos_auditor._internal.compat import check_compat


class _ToolReportSuppressed:
    """Wraps a `FrameworkAuditSession` for use as the `session=` kwarg on
    `kit.tools` functions called through LangGraph/LangChain.

    `kit.tools` functions self-report every call via `session.record_tool(...)`
    (see `kit/tools.py`'s `_report()`) so the package stays importable with
    zero dependency on `dashboard/`. But LangGraph *also* auto-dispatches
    `BaseCallbackHandler.on_tool_start`/`on_tool_end` for every tool
    invocation, and `LangGraphAuditHandler.on_tool_end` (below) independently
    calls `self.session.record_tool(...)` too. Passing the *same* session
    into both paths (as `scripts/kit_demo/run_live_enterprise.py` did)
    double-appended every real tool call into `_pending_tools`, so each
    persisted turn's `tool_calls` list showed every call twice — this was
    mistaken in kit-035 for gpt-5-mini "duplicating tool calls", when in
    fact it's an audit-instrumentation artifact present for every model on
    every kit-arm run (confirmed real per-turn `usage.total_tokens`, which
    comes from `on_llm_end`'s `AIMessage.usage_metadata` on a wholly separate
    code path, is unaffected — this bug only doubled the *display* list).

    This proxy forwards every attribute kit.tools' guards need
    (`dedupe_guard`, `cache_guard`) straight through to the real session, but
    makes `record_tool` a no-op, so the *only* recording path left for a
    LangGraph-driven kit tool call is the callback (`on_tool_end`) — exactly
    one row per real call.
    """

    def __init__(self, session: FrameworkAuditSession) -> None:
        self._session = session

    def record_tool(self, name: str, args: Any, result: Any) -> None:
        return None

    def __getattr__(self, item: str) -> Any:
        return getattr(self._session, item)


class AuditorCallback(BaseCallbackHandler):
    def __init__(
        self,
        *,
        model_hint: str | None = None,
        task: str = "langgraph run",
        out_dir=None,
        session_id: str | None = None,
        arm: str = "baseline",
        workspace_root: str | None = None,
    ) -> None:
        super().__init__()
        check_compat("langgraph")
        self.session = FrameworkAuditSession(
            framework="langgraph",
            model=model_hint,
            task=task,
            out_dir=out_dir,
            session_id=session_id,
            arm=arm,
            workspace_root=workspace_root,
        )
        self._pending_tool_starts: dict[UUID, dict[str, Any]] = {}

    @property
    def tool_session(self) -> _ToolReportSuppressed:
        """The `session=` to hand to `kit.tools`/`build_langchain_kit_tools`
        callers. See `_ToolReportSuppressed` docstring: keeps guard state
        (dedupe/cache) live while avoiding the double-record with
        `on_tool_end` below."""
        return _ToolReportSuppressed(self.session)

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name = (serialized or {}).get("name") or "tool"
        args: Any = inputs if inputs is not None else {"input": input_str}
        self._pending_tool_starts[run_id] = {"name": name, "args": args}

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._pending_tool_starts.pop(run_id, None)
        if started is None:
            return
        result_text = getattr(output, "content", output)
        self.session.record_tool(started["name"], started["args"], result_text)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        usage, model = self._extract_usage(response)
        self.session.record_llm(usage, model)

    @staticmethod
    def _extract_usage(response: Any) -> tuple[dict[str, Any], str | None]:
        """Prefer langchain_core's cross-provider UsageMetadata
        (AIMessage.usage_metadata) — the same field regardless of whether
        the underlying model is OpenAI, Anthropic, Gemini, etc. Fall back to
        the legacy provider-specific llm_output.token_usage dict."""
        model = None
        try:
            generations = response.generations or []
            message = generations[0][0].message
            usage_metadata = getattr(message, "usage_metadata", None)
            model = (getattr(message, "response_metadata", None) or {}).get("model_name")
            if usage_metadata:
                return dict(usage_metadata), model
        except (AttributeError, IndexError, TypeError):
            pass
        llm_output = getattr(response, "llm_output", None) or {}
        token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        return dict(token_usage), llm_output.get("model_name") or model

    def finish(self, *, success: bool | None = None, error: str = "") -> None:
        self.session.finish(success=success, error=error)


# Back-compat alias matching the monorepo's internal name.
LangGraphAuditHandler = AuditorCallback
