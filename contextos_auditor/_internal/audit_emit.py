"""Canonical append-only audit session writer."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
import warnings
from pathlib import Path
from typing import Any

# PERF: minimum seconds between two throttled session.json rewrites during a
# run. Chosen so a live `watch`/`serve` dashboard still sees the session's
# mtime advance well inside its refresh interval, while a fast agent doing
# hundreds of turns per second pays for one metadata write instead of
# hundreds. Terminal writes (finish/error) bypass this entirely.
_SESSION_WRITE_INTERVAL = 1.0


class AuditSession:
    def __init__(
        self,
        root: Path,
        *,
        session_id: str,
        model: str,
        task: str,
        arm: str = "baseline",
        framework: str = "direct-api",
    ) -> None:
        self.dir = Path(root) / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.session_path = self.dir / "session.json"
        self.session_id = session_id
        self.model = model
        self.task = task
        self.arm = arm
        self.framework = framework
        self.status = "running"
        self.started_at = time.time()
        # Framework adapters (crewai_adapter, etc.) hook event buses that
        # dispatch sync handlers via a ThreadPoolExecutor, so two
        # record_llm/emit_turn calls can genuinely run concurrently on the
        # same AuditSession. Serialize file writes so the shared
        # session.<pid>.tmp rename can't race across threads.
        self._lock = threading.Lock()
        # PERF: throttles the per-turn session.json rewrite (see emit_turn).
        # Starts at 0.0 so the constructor's write below always happens --
        # a session directory with no session.json is not discoverable.
        self._last_session_write = 0.0
        self._write_session()

    def emit_turn(
        self,
        turn: int,
        *,
        usage: dict[str, Any],
        cost: dict[str, Any],
        tool_calls: list[dict[str, Any]],
        synthetic: bool = False,
    ) -> None:
        # Strip huge result bodies from non-read tools; keep read/write content
        # needed for shadow_kit (bounded).
        slim: list[dict[str, Any]] = []
        for t in tool_calls:
            row = {
                "name": t.get("name"),
                "args": t.get("args") or {},
                "chars": t.get("chars"),
                "tokens": t.get("tokens"),
            }
            name = t.get("name")
            # Bounded result_text for every tool, not just read_file: KIT-504
            # (dashboard.shadow_kit's levers_fired) needs to see the real
            # Kit confirmation strings (edit_file/smart_patch/grep/list_dir/
            # find_def/find_refs/run_shell all report what they did in a
            # short marker string) to tell "lever fired" from "tool was
            # merely called". These are cheap: unlike read_file's full file
            # body, every other Kit tool's result is already a short,
            # human-scale confirmation, not raw file content.
            text = t.get("result_text") or t.get("output") or ""
            if isinstance(text, str) and len(text) > 120_000:
                text = text[:120_000]
            row["result_text"] = text
            if name == "write_file":
                args = dict(row["args"])
                content = str(args.get("content") or "")
                if len(content) > 120_000:
                    args["content"] = content[:120_000]
                row["args"] = args
            slim.append(row)
        event = {
            "kind": "turn",
            "turn": turn,
            "ts": time.time(),
            "usage": {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            },
            "cost": {
                "total_nano_aiu": int(cost.get("total_nano_aiu") or 0),
                "input_tokens": int(cost.get("input_tokens") or 0),
                "output_tokens": int(cost.get("output_tokens") or 0),
                "cache_read_tokens": int(cost.get("cache_read_tokens") or 0),
                "cache_write_tokens": int(cost.get("cache_write_tokens") or 0),
                # AUD-011: dated, sourced $ estimate (see _internal/pricing.py) --
                # None (not 0) when this session's model has no citable
                # pricing snapshot, so shadow_kit never silently treats
                # "unpriced" as "free".
                "estimated_usd": cost.get("estimated_usd"),
            },
            "tool_calls": slim,
        }
        if synthetic:
            # BUG-B5: not a model round-trip -- a flush of tool calls that
            # happened after the last LLM call. Written only when true so
            # existing sessions stay byte-identical and readers can treat a
            # missing key as False.
            event["synthetic"] = True
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
            # PERF: session.json is pure metadata -- its payload is
            # byte-identical from one turn to the next except `updated_at`,
            # which nothing reads. Rewriting it (tmp write + fsync-less
            # atomic rename) on *every* turn was 28% of the auditor's total
            # CPU (measured: 0.33s of `posix.replace` + ~0.19s of opens over
            # 1500 turns). events.jsonl above is the durable record of every
            # turn and is still appended synchronously, so throttling this
            # loses nothing: the only consumer of session.json's freshness
            # is cli._all_session_dirs, which orders sessions by mtime.
            self._write_session(throttle=True)

    def finish(self, *, success: bool | None = None, error: str = "") -> None:
        self.status = "finished" if not error else "error"
        with self._lock:
            # Never throttled: this is the write that records the terminal
            # status, and no later write will correct it.
            self._write_session(success=success, error=error)

    def _write_session(
        self, *, success: bool | None = None, error: str = "", throttle: bool = False
    ) -> None:
        now = time.time()
        if throttle and (now - self._last_session_write) < _SESSION_WRITE_INTERVAL:
            return
        self._last_session_write = now
        payload = {
            "id": self.session_id,
            "model": self.model,
            "task": self.task,
            "arm": self.arm,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": now,
            "success": success,
            "error": error,
            "product": "agent-auditor",
            "pricing": "free",
            "framework": self.framework,
        }
        tmp = self.session_path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.session_path)


def load_events_with_stats(session_dir: Path) -> tuple[list[dict], int]:
    """BUG-F-002/C5: a partially-written final line is the NORMAL state of
    an events.jsonl that is being appended to live (`watch`/`serve`/
    `report` all read a file the agent is still writing) or of one whose
    writer was killed mid-write. `json.loads` on that torn line used to
    raise, which made every *good* turn in the file unreachable: `report`
    exited 1 with a traceback and a `watch --serve` dashboard stopped
    answering HTTP entirely.

    Malformed lines are skipped, but never silently: the count is returned
    alongside the events so callers can say "N unreadable lines skipped".
    A truncated session must never masquerade as a complete one."""
    path = session_dir / "events.jsonl"
    if not path.is_file():
        return [], 0
    try:
        # BUG-F-001: explicit utf-8, never the platform locale encoding --
        # events carry non-ASCII tool args/paths on any platform.
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Unreadable file (permissions, vanished mid-read) is reported the
        # same way a torn line is: no events, and a visible non-zero count.
        return [], 1
    out: list[dict] = []
    skipped = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
        else:
            # A well-formed JSON scalar/array is still not an event record;
            # downstream code indexes it like a dict, so count it unreadable.
            skipped += 1
    return out, skipped


def load_events(session_dir: Path) -> list[dict]:
    """Return readable events, warning if the list is incomplete.

    Callers rendering their own incomplete-data banner should instead use
    ``load_events_with_stats`` to avoid a duplicate warning.
    """
    events, skipped = load_events_with_stats(session_dir)
    if skipped:
        warnings.warn(
            f"contextos-auditor: {skipped} unreadable lines skipped in "
            f"{session_dir / 'events.jsonl'}; audit totals are incomplete",
            stacklevel=2,
        )
    return events
