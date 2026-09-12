"""Standalone implementation of the session duplicate-tool-call contract.

Per-session duplicate-tool-call detector. Kept here mainly so
`FrameworkAuditSession` (in `_internal/base.py`) has a `dedupe_guard`
attribute for parity with the monorepo's adapters — it is not required for
the Auditor's own read-only observation and estimation, only consulted by
tools that explicitly opt in via `getattr(session, "dedupe_guard", None)`.
Write-loop counters and post-write confirmation methods keep sessions
compatible with tools implementing the same contract, without importing
the optimiser or making it a dependency.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# BUG-B7: both maps below only ever grew. A long-running agent doing
# thousands of distinct reads kept every signature (built from args that can
# include 120KB file bodies) alive for the process lifetime. Bound them.
_MAX_TRACKED = 10_000

# `repr()` of an object without a custom __repr__ ends in ` at 0x7f...`,
# which differs on every allocation. Any signature containing one is unique
# by construction and can never match a later identical call.
_ADDR_RE = re.compile(r" at 0x[0-9a-fA-F]+")


def _stable_default(obj: Any) -> Any:
    """JSON fallback for values `json` cannot encode.

    BUG-B6 (second half): the original fix passed `default=repr`, which
    still embedded a memory address for every ordinary object -- so tool
    args holding a Pydantic model, a dataclass instance or any custom type
    (which is most of what agent frameworks actually pass) continued to
    never deduplicate. Prefer the object's *structure*, and fall back to a
    repr with the address stripped out.
    """
    try:
        attrs = vars(obj)
    except TypeError:
        attrs = None
    if isinstance(attrs, dict):
        # Returned as a dict so json recurses into it and `sort_keys` still
        # applies, keeping the signature order-independent all the way down.
        return {"__type__": type(obj).__name__, "__dict__": attrs}
    return f"{type(obj).__name__}:{_ADDR_RE.sub('', repr(obj))}"


def _signature(name: str, args: dict[str, Any]) -> tuple:
    """Canonical, order-independent signature for a tool call.

    BUG-B6: this used to be `tuple(sorted((k, repr(v)) ...))`. `repr` of any
    object without a custom `__repr__` embeds its memory address, so two
    structurally identical calls produced different signatures and never
    deduped; and nested dicts compared by insertion order, so
    `{"a":1,"b":2}` and `{"b":2,"a":1}` were "different" calls. Serialising
    with sorted keys fixes both, and is stable across the whole structure
    rather than just the top level.
    """
    try:
        canon = json.dumps(args, sort_keys=True, default=_stable_default)
    except (TypeError, ValueError):
        # Self-referential or otherwise unserialisable args: fall back to
        # the old behaviour rather than raising into the agent's hot path.
        canon = _ADDR_RE.sub(
            "", repr(sorted((str(k), repr(v)) for k, v in args.items()))
        )
    return (name, canon)


@dataclass
class DedupeGuard:
    WRITE_DEDUPE_TTL_S = 3.0
    POST_WRITE_READ_WINDOW_CALLS = 2
    HARD_BLOCK_AFTER_AMBIGUOUS_FAILURES = 2

    _seen: OrderedDict[tuple, int] = field(default_factory=OrderedDict)
    _epoch: int = 0
    _write_seen: OrderedDict[tuple, float] = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _call_seq: int = 0
    _post_write: OrderedDict[str, tuple[int, str]] = field(default_factory=OrderedDict)
    _ambiguous_fail_count: OrderedDict[str, int] = field(default_factory=OrderedDict)

    def _remember(self, store: OrderedDict, key: Any, value: Any) -> None:
        store[key] = value
        store.move_to_end(key)
        while len(store) > _MAX_TRACKED:
            store.popitem(last=False)

    def check(self, name: str, args: dict[str, Any]) -> bool:
        sig = _signature(name, args)
        with self._lock:
            self._call_seq += 1
            is_dup = self._seen.get(sig) == self._epoch
            self._remember(self._seen, sig, self._epoch)
            return is_dup

    def bump(self) -> None:
        with self._lock:
            self._epoch += 1

    def note_ambiguous_edit_failure(self, path: str) -> int:
        """Count consecutive ambiguous anchors until a successful write."""
        with self._lock:
            count = self._ambiguous_fail_count.get(path, 0) + 1
            self._remember(self._ambiguous_fail_count, path, count)
            return count

    def ambiguous_failures_for(self, path: str) -> int:
        with self._lock:
            return self._ambiguous_fail_count.get(path, 0)

    def reset_ambiguous_failures(self, path: str) -> None:
        with self._lock:
            self._ambiguous_fail_count.pop(path, None)

    def note_successful_write(self, path: str, summary: str) -> None:
        key = str(Path(path).resolve())
        with self._lock:
            self._remember(self._post_write, key, (self._call_seq, summary))

    def check_post_write_read(self, path: str) -> str | None:
        """Return a recent write confirmation once; a second read gets bytes."""
        key = str(Path(path).resolve())
        with self._lock:
            entry = self._post_write.pop(key, None)
            if entry is None:
                return None
            sequence, summary = entry
            if self._call_seq - sequence > self.POST_WRITE_READ_WINDOW_CALLS:
                return None
            return summary

    def check_write(self, name: str, args: dict[str, Any], *, ttl: float | None = None) -> bool:
        ttl = self.WRITE_DEDUPE_TTL_S if ttl is None else ttl
        sig = _signature(name, args)
        now = time.monotonic()
        with self._lock:
            # Purge entries whose TTL has already expired; without this they
            # accumulated forever even though they could never match again.
            if len(self._write_seen) > _MAX_TRACKED // 2:
                for key in [k for k, t in self._write_seen.items() if (now - t) >= ttl]:
                    del self._write_seen[key]
            last = self._write_seen.get(sig)
            is_dup = last is not None and (now - last) < ttl
            if not is_dup:
                self._remember(self._write_seen, sig, now)
            return is_dup
