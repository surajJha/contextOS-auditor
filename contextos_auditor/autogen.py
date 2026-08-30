"""Free Auditor adapter for AutoGen (AGNext: `autogen-core` / `autogen-agentchat`).

Unlike CrewAI (global event bus), OpenAI Agents SDK (global tracing
provider), or LangChain (per-invocation callback handler), AGNext has no
single global observability hook — usage lives on the return value of
`ChatCompletionClient.create()`, and tool execution happens as plain Python
calls inside `AssistantAgent`, not through the client at all. So this
adapter is two small pieces instead of one:

1. `AuditingChatCompletionClient` — wraps whatever real client an
   `AssistantAgent` is configured with (`OpenAIChatCompletionClient`, etc.)
   and records LLM usage after every `create()`/`create_stream()` call.
2. `audit_tool()` — wraps an individual tool callable so its real
   name/args/result reach the same session, since AGNext tools are just
   plain functions (or `FunctionTool`s) passed to `AssistantAgent(tools=...)`.

    from contextos_auditor.autogen import new_session, wrap_client, audit_tool
    session = new_session(model="gpt-4o", task="...")
    client = wrap_client(real_client, session)
    agent = AssistantAgent("coder", model_client=client, tools=[audit_tool(write_file, session)])
    ...
    session.finish()

Requires: `pip install contextos-auditor[autogen]`
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

from autogen_core.models import ChatCompletionClient

from contextos_auditor._internal.base import FrameworkAuditSession, _warn_once, guarded
from contextos_auditor._internal.compat import check_compat


class AuditingChatCompletionClient(ChatCompletionClient):
    """Transparent proxy: every abstract method delegates to the wrapped
    client unchanged; only create()/create_stream() also record usage."""

    def __init__(self, wrapped: ChatCompletionClient, session: FrameworkAuditSession) -> None:
        self._wrapped = wrapped
        self._session = session

    @guarded("autogen._record")
    def _record(self, result: Any) -> None:
        usage = getattr(result, "usage", None)
        model = getattr(self._wrapped, "model_info", {}) or {}
        model_name = model.get("family") if isinstance(model, dict) else None
        usage_dict = {}
        if usage is not None:
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
            }
        self._session.record_llm(usage_dict, model_name)

    async def create(self, *args: Any, **kwargs: Any):
        result = await self._wrapped.create(*args, **kwargs)
        self._record(result)
        return result

    async def create_stream(self, *args: Any, **kwargs: Any):
        last: Any = None
        async for chunk in self._wrapped.create_stream(*args, **kwargs):
            last = chunk
            yield chunk
        if last is not None and hasattr(last, "usage"):
            self._record(last)

    def actual_usage(self):
        return self._wrapped.actual_usage()

    def total_usage(self):
        return self._wrapped.total_usage()

    def count_tokens(self, *args: Any, **kwargs: Any) -> int:
        return self._wrapped.count_tokens(*args, **kwargs)

    def remaining_tokens(self, *args: Any, **kwargs: Any) -> int:
        return self._wrapped.remaining_tokens(*args, **kwargs)

    async def close(self) -> None:
        await self._wrapped.close()

    @property
    def capabilities(self):
        return self._wrapped.capabilities

    @property
    def model_info(self):
        return self._wrapped.model_info


def audit_tool(fn: Any, session: FrameworkAuditSession):
    """Wrap a plain tool callable (sync or async) so real invocations reach
    the Auditor. Name/args/result flow through unchanged to the wrapped fn;
    this only ever observes, never alters behavior.

    AGNext builds each tool's pydantic args schema straight from
    `inspect.signature(fn)`, so a naive `(*args, **kwargs)` wrapper would
    replace the real parameter names (`path`, `content`, ...) with literal
    `args`/`kwargs` fields and break every call. `functools.wraps` sets
    `__wrapped__`, which `inspect.signature` follows by default — that's
    what keeps the original signature visible to AGNext's schema builder.
    """
    name = getattr(fn, "__name__", "tool")

    def _safe_record(bound: dict[str, Any], result: Any) -> None:
        # AUD-009: belt-and-suspenders -- FrameworkAuditSession.record_tool
        # is already @guarded, but `session` here is a duck-typed parameter
        # (anything with a record_tool(name, args, result) method), so this
        # wrapper cannot assume every caller's session object is equally
        # defensive. The real tool call above has already completed and
        # `result` must reach the caller no matter what happens here.
        try:
            session.record_tool(name, bound, result)
        except Exception as exc:  # noqa: BLE001 -- intentional, see above
            _warn_once(f"autogen.audit_tool({name!r})", exc)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def _async_wrapped(*args: Any, **kwargs: Any):
            bound = _bind_args(fn, args, kwargs)
            result = await fn(*args, **kwargs)
            _safe_record(bound, result)
            return result

        return _async_wrapped

    @functools.wraps(fn)
    def _sync_wrapped(*args: Any, **kwargs: Any):
        bound = _bind_args(fn, args, kwargs)
        result = fn(*args, **kwargs)
        _safe_record(bound, result)
        return result

    return _sync_wrapped


def _bind_args(fn: Any, args: tuple, kwargs: dict) -> dict[str, Any]:
    """Best-effort: resolve positional args to parameter names too, so
    record_tool always sees a name-keyed dict (needed for the path/content
    lookups shadow_kit relies on) regardless of how the caller invoked fn.

    AUD-009: runs *before* the real tool call, so any exception here --
    not just the expected TypeError from a signature mismatch -- must never
    block the real `fn(*args, **kwargs)` call below from executing."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        return dict(bound.arguments)
    except Exception:  # noqa: BLE001 -- intentional, see docstring
        return dict(kwargs) or {"args": args}


def new_session(
    *,
    model: str | None = None,
    task: str = "autogen run",
    out_dir=None,
    arm: str = "baseline",
    session_id: str | None = None,
    otel_endpoint: str | None = None,
    redact_secrets: bool | None = None,
) -> FrameworkAuditSession:
    """Convenience constructor -- AutoGen has no single global hook to
    attach to (see module docstring), so the session is built explicitly
    and threaded through `wrap_client`/`audit_tool` by hand."""
    check_compat("autogen")
    return FrameworkAuditSession(
        framework="autogen", model=model, task=task, out_dir=out_dir,
        arm=arm, session_id=session_id, otel_endpoint=otel_endpoint, redact_secrets=redact_secrets,
    )


# Back-compat alias matching the monorepo's internal name.
wrap_client = AuditingChatCompletionClient
