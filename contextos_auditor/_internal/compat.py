"""AUD-007: SDK-version drift is a real, quiet failure mode for an
observation-only adapter -- if a framework changes an event's field names
or a client wrapper's method signature between versions, the Auditor can
silently under-count tokens/tool calls instead of raising a loud error,
because it never sits in the agent's execution path (that's the whole
point of it being unobtrusive).

This module is the single place that knows which SDK versions each
vendored adapter was actually tested against. It never blocks or raises --
an outdated/newer SDK should still be usable -- it only emits one
`UserWarning` per (framework, installed version) so a user investigating
"my savings numbers look wrong" has an obvious first thing to check, and
`contextos-auditor doctor` surfaces the same information proactively.
"""

from __future__ import annotations

import importlib
import warnings
from dataclasses import dataclass


@dataclass(frozen=True)
class _TestedRange:
    module: str
    min_version: tuple[int, ...]
    max_version_exclusive: tuple[int, ...]


# Update these when the corresponding _internal/*.py vendor copy is
# re-synced against a newer framework release and re-verified against its
# real test suite (see each adapter's module docstring + the README's
# "vendored, not auto-synced" disclaimer).
_TESTED: dict[str, _TestedRange] = {
    "crewai": _TestedRange("crewai", (0, 100, 0), (2, 0, 0)),
    "langgraph": _TestedRange("langchain_core", (0, 3, 0), (2, 0, 0)),
    "openai_agents": _TestedRange("agents", (0, 1, 0), (1, 0, 0)),
    "autogen": _TestedRange("autogen_core", (0, 4, 0), (1, 0, 0)),
}

_warned: set[str] = set()


def _parse_version(raw: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in raw.split(".")[:3]:
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def installed_version(framework: str) -> str | None:
    """Best-effort installed-version lookup for the SDK module backing
    `framework` (one of "crewai"/"langgraph"/"openai_agents"/"autogen").
    Returns None if the SDK isn't installed at all."""
    spec = _TESTED.get(framework)
    if spec is None:
        return None
    try:
        mod = importlib.import_module(spec.module)
    except ImportError:
        return None
    return getattr(mod, "__version__", None)


def check_compat(framework: str) -> None:
    """Warn (once per framework per process) if the installed SDK version
    falls outside the range this adapter was last verified against. Never
    raises -- an out-of-range SDK is a "double-check your numbers" signal,
    not a hard failure, since many minor-version bumps won't actually
    change the event shapes this adapter reads."""
    spec = _TESTED.get(framework)
    if spec is None or framework in _warned:
        return

    raw = installed_version(framework)
    if raw is None:
        return  # not installed / no __version__ -- nothing useful to say

    version = _parse_version(raw)
    if spec.min_version <= version < spec.max_version_exclusive:
        return

    _warned.add(framework)
    tested_lo = ".".join(str(p) for p in spec.min_version)
    tested_hi = ".".join(str(p) for p in spec.max_version_exclusive)
    warnings.warn(
        f"contextos-auditor: {spec.module} {raw} is outside the range this "
        f"{framework} adapter was last verified against ([{tested_lo}, {tested_hi})). "
        "It will likely still work, but if the numbers you see look wrong, "
        "this version drift is the first thing to check -- run "
        "`contextos-auditor doctor` for the full compatibility report.",
        stacklevel=3,
    )
