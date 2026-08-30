"""Vendored from `dashboard/audit_emit.py` in the toku monorepo. Append-only
audit session writer -- unchanged logic, copied so this package installs
standalone outside the monorepo (see package README for the vendoring note).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any


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
        self._write_session()

    def emit_turn(
        self,
        turn: int,
        *,
        usage: dict[str, Any],
        cost: dict[str, Any],
        tool_calls: list[dict[str, Any]],
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
        with self._lock:
            with self.events_path.open("a") as f:
                f.write(json.dumps(event, default=str) + "\n")
            self._write_session()

    def finish(self, *, success: bool | None = None, error: str = "") -> None:
        self.status = "finished" if not error else "error"
        with self._lock:
            self._write_session(success=success, error=error)

    def _write_session(
        self, *, success: bool | None = None, error: str = ""
    ) -> None:
        payload = {
            "id": self.session_id,
            "model": self.model,
            "task": self.task,
            "arm": self.arm,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": time.time(),
            "success": success,
            "error": error,
            "product": "agent-auditor",
            "pricing": "free",
            "framework": self.framework,
        }
        tmp = self.session_path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(self.session_path)


def load_events(session_dir: Path) -> list[dict]:
    path = session_dir / "events.jsonl"
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out
