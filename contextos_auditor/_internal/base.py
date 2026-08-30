"""Vendored from `dashboard/adapters/base.py` in the toku monorepo --
unchanged logic (default session directory adjusted for standalone install:
`./.contextos/audit` instead of `./out/audit`). See this package's README
for the vendoring note.

Each framework adapter (crewai.py, langgraph.py, openai_agents.py,
autogen.py) hooks that framework's native callback/event/tracing system and
forwards two kinds of observations here:

- an LLM call finishing, with whatever usage dict the framework exposes
- a tool call finishing, with its name/args/result

`FrameworkAuditSession` normalizes both into the same `AuditSession.emit_turn`
shape `contextos_auditor._internal.audit_emit` understands, so
`contextos_auditor._internal.shadow_kit` needs no framework-specific code.
"""

from __future__ import annotations

import threading
import time
import warnings
from pathlib import Path
from typing import Any

from contextos_auditor._internal.audit_emit import AuditSession
from contextos_auditor._internal.dedupe import DedupeGuard

# Tool-name aliases the file-write/read detector in shadow_kit.py understands.
# Framework tool names vary (write_file, WriteFileTool, apply_patch, ...); we
# normalize the common file-I/O ones so shadow_kit can still reconstruct
# file_state and estimate write_file -> edit_file waste. Anything else passes
# through verbatim as an opaque tool call (still visible in the UI, just not
# counted toward the write-waste estimate).
# NOTE when reading traces: this normalization is lossy in the persisted
# events.jsonl, so a kit-arm run that called `create_file` shows up as
# `write_file` -- a name the kit does not even expose. That looks alarming
# ("the kit arm called a baseline tool!") and is not: it is this alias map.
# Verified 2026-08-16 by invoking the kit's create_file tool directly and
# watching it record as write_file.
_WRITE_ALIASES = {"write_file", "writefile", "write_to_file", "create_file", "update_file"}
_READ_ALIASES = {"read_file", "readfile", "read_text_file"}

# AUD-009: this adapter observes a live, real agent run from inside its own
# process (registered directly on the framework's event bus / callback
# manager / model-client wrapper). If any of *our* code raises -- a
# malformed usage dict, an unexpected event shape from a newer/older SDK
# version, a full disk -- that exception must never propagate back into the
# agent's real execution path. An observability tool that can crash the
# thing it observes is disqualifying for production use, full stop.
#
# `guarded` wraps every adapter handler method and every FrameworkAuditSession
# entry point below: on failure it emits one `UserWarning` per distinct
# label per process (so failures stay discoverable, not silently swallowed
# forever) and returns None instead of raising.
_warned_labels: set[str] = set()


def _warn_once(label: str, exc: Exception) -> None:
    if label in _warned_labels:
        return
    _warned_labels.add(label)
    warnings.warn(
        f"contextos-auditor: internal error in {label}, this observation was "
        f"dropped but your agent's real run is unaffected ({exc.__class__.__name__}: {exc}). "
        "This warning only appears once per process; run with "
        "`python -W always::UserWarning` for every occurrence.",
        stacklevel=3,
    )


def guarded(label: str):
    """Decorator: never let an exception out of the wrapped function. See
    the module-level comment above for why this is not optional."""

    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 -- intentional, see docstring
                _warn_once(label, exc)
                return None

        wrapper.__name__ = getattr(fn, "__name__", label)
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


def _normalize_tool_name(name: str) -> str:
    key = (name or "").strip().lower()
    if key in _WRITE_ALIASES:
        return "write_file"
    if key in _READ_ALIASES:
        return "read_file"
    return name or "tool"


def _extract_path(args: dict[str, Any]) -> str | None:
    for key in ("path", "file_path", "filename", "file_name"):
        if args.get(key):
            return str(args[key])
    return None


def _extract_write_content(args: dict[str, Any]) -> str | None:
    for key in ("content", "text", "new_str", "new_content"):
        if args.get(key) is not None:
            return str(args[key])
    return None


class FrameworkAuditSession:
    """Thin wrapper an adapter calls; keeps each adapter file tiny."""

    def __init__(
        self,
        *,
        framework: str,
        model: str | None,
        task: str,
        out_dir: Path | None = None,
        session_id: str | None = None,
        arm: str = "baseline",
        workspace_root: str | Path | None = None,
    ) -> None:
        root = out_dir or (Path.cwd() / ".contextos" / "audit")
        sid = session_id or f"{framework}-{int(time.time() * 1000)}"
        self._session = AuditSession(
            root,
            session_id=sid,
            model=model or "unknown",
            task=task[:240],
            arm=arm,
            framework=framework,
        )
        self._turn = 0
        self._pending_tools: list[dict[str, Any]] = []
        # AUDIT-001: opt-in confinement for kit/tools.py's path-touching
        # functions (getattr(session, "workspace_root", None), same
        # zero-cost-when-absent pattern as dedupe_guard below). None here
        # means "no confinement", matching every caller from before this
        # existed.
        self.workspace_root = workspace_root
        # KIT-006: every FrameworkAuditSession carries a live DedupeGuard so
        # `kit.tools`' read-only tools (read_file/grep/list_dir/find_def/
        # find_refs) automatically short-circuit an identical back-to-back
        # repeat instead of recomputing/resending the same result — this is
        # what makes the fix apply to real live runs (e.g.
        # scripts/kit_demo/run_live_enterprise.py) with no per-script
        # wiring, not just to unit tests that build one explicitly. Purely
        # additive: a session this is passed to only ever consults it via
        # `getattr(session, "dedupe_guard", None)`, so anything that never
        # looks for the attribute (e.g. this class's own record_tool below)
        # is completely unaffected by its presence.
        self.dedupe_guard = DedupeGuard()
        # Framework event buses commonly dispatch callbacks from a thread
        # pool (e.g. CrewAI's crewai_event_bus submits sync handlers via
        # ThreadPoolExecutor) rather than calling them inline, so two
        # record_* calls for genuinely sequential events can still race on
        # this buffer. A lock keeps each call atomic; it does not by itself
        # guarantee cross-call ordering — callers that need strict ordering
        # under a threaded bus should serialize their own emits (see
        # crewai_adapter's test for the direct-call pattern that sidesteps
        # this entirely).
        self._lock = threading.Lock()

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @guarded("FrameworkAuditSession.record_tool")
    def record_tool(self, name: str, args: Any, result: Any) -> None:
        """Buffer a tool call; flushed into the next `record_llm` turn.

        Tool calls between two LLM calls are attributed to the *next* LLM
        turn's event (matching how the observation-only harness scripts
        already interleave usage + tool_calls — see scripts/copilot_ctx_ab.py).
        """
        args_dict = args if isinstance(args, dict) else {"value": args}
        norm_name = _normalize_tool_name(name)
        row: dict[str, Any] = {"name": norm_name, "args": args_dict}
        # KIT-504: every tool's result_text gets forwarded (audit_emit.py
        # bounds it further before persisting), not just read_file's — Kit's
        # own lever tools (edit_file/smart_patch/grep/list_dir/find_def/
        # find_refs/run_shell) report what they actually did in a short
        # confirmation string, and dashboard.shadow_kit's levers_fired needs
        # that text to tell "fired" from "merely called, but failed/no-op".
        row["result_text"] = result if isinstance(result, str) else str(result)
        if norm_name == "write_file":
            path = _extract_path(args_dict)
            content = _extract_write_content(args_dict)
            if path is not None and content is not None:
                row["args"] = {"path": path, "content": content}
        row["chars"] = len(str(result)) if result is not None else 0
        with self._lock:
            self._pending_tools.append(row)

    @guarded("FrameworkAuditSession.record_llm")
    def record_llm(self, usage: dict[str, Any] | None, model: str | None = None) -> None:
        """Flush buffered tool calls together with this LLM call's usage."""
        usage = usage or {}
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        total = int(usage.get("total_tokens") or (prompt + completion))
        with self._lock:
            self._turn += 1
            turn = self._turn
            tool_calls = self._pending_tools
            self._pending_tools = []
        self._session.emit_turn(
            turn,
            usage={
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
            },
            cost={"total_nano_aiu": 0},
            tool_calls=tool_calls,
        )

    @guarded("FrameworkAuditSession.finish")
    def finish(self, *, success: bool | None = None, error: str = "") -> None:
        with self._lock:
            leftover = self._pending_tools
            self._pending_tools = []
            if leftover:
                self._turn += 1
                turn = self._turn
        if leftover:
            self._session.emit_turn(
                turn,
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                cost={"total_nano_aiu": 0},
                tool_calls=leftover,
            )
        self._session.finish(success=success, error=error)
