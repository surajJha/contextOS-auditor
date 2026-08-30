"""Vendored (trimmed) from `kit/dedupe.py` in the toku monorepo.

Per-session duplicate-tool-call detector. Kept here mainly so
`FrameworkAuditSession` (in `_internal/base.py`) has a `dedupe_guard`
attribute for parity with the monorepo's adapters — it is not required for
the Auditor's own read-only observation and estimation, only consulted by
tools that explicitly opt in via `getattr(session, "dedupe_guard", None)`.
See the monorepo's `kit/dedupe.py` for the full incident history (KIT-006
through KIT-039) this design encodes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


def _signature(name: str, args: dict[str, Any]) -> tuple:
    return (name, tuple(sorted((k, repr(v)) for k, v in args.items())))


@dataclass
class DedupeGuard:
    WRITE_DEDUPE_TTL_S = 3.0

    _seen: dict[tuple, int] = field(default_factory=dict)
    _epoch: int = 0
    _write_seen: dict[tuple, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, name: str, args: dict[str, Any]) -> bool:
        sig = _signature(name, args)
        with self._lock:
            is_dup = self._seen.get(sig) == self._epoch
            self._seen[sig] = self._epoch
            return is_dup

    def bump(self) -> None:
        with self._lock:
            self._epoch += 1

    def check_write(self, name: str, args: dict[str, Any], *, ttl: float | None = None) -> bool:
        ttl = self.WRITE_DEDUPE_TTL_S if ttl is None else ttl
        sig = _signature(name, args)
        now = time.monotonic()
        with self._lock:
            last = self._write_seen.get(sig)
            is_dup = last is not None and (now - last) < ttl
            if not is_dup:
                self._write_seen[sig] = now
            return is_dup
