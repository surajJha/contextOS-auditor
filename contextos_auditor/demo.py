"""A zero-config, zero-dependency demo session (LNCH-006).

Why this exists: every other path into this tool assumes you already have
a working CrewAI/LangGraph/AutoGen/OpenAI-Agents agent wired up. Someone
who just found the project has no way to see what it actually produces
without first building an agent -- which is a lot to ask before you've
been shown anything. `contextos-auditor demo` closes that gap: it
synthesizes a realistic agent session and hands it to the same renderer
the real thing uses, so the first run shows a populated dashboard in a
couple of seconds with no SDK, no API key, and no network.

Honesty constraints this module is bound by:

- The session it writes is clearly marked as synthetic (`task` says so,
  the session id is prefixed `demo-`) so a demo run can never be mistaken
  for, or quoted as, a measurement of a real agent.
- The waste it contains is *not* faked. It writes ordinary read/write
  tool calls into the normal event stream and lets `shadow_kit` derive
  the numbers exactly as it would for a real run. The scenario is
  chosen to be representative, but the arithmetic is the product's, not
  a hardcoded result.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from contextos_auditor._internal.base import FrameworkAuditSession

DEMO_MODEL = "gpt-4o-mini"
DEMO_TASK = "[synthetic demo session] Fix the failing checkout retry test"

# A plausible source file the imaginary agent is working on. Big enough
# that rewriting it wholesale is visibly wasteful next to a two-line edit,
# which is the exact pattern the optimiser removes.
_CONFIG_PY = textwrap.dedent(
    '''
    """Checkout retry policy."""

    from dataclasses import dataclass


    @dataclass(frozen=True)
    class RetryPolicy:
        max_attempts: int = 3
        base_delay_seconds: float = 0.5
        max_delay_seconds: float = 8.0
        jitter: bool = True
        retry_on_status: tuple[int, ...] = (429, 500, 502, 503, 504)

        def delay_for(self, attempt: int) -> float:
            if attempt <= 0:
                return 0.0
            raw = self.base_delay_seconds * (2 ** (attempt - 1))
            return min(raw, self.max_delay_seconds)

        def should_retry(self, attempt: int, status: int) -> bool:
            if attempt >= self.max_attempts:
                return False
            return status in self.retry_on_status


    DEFAULT_POLICY = RetryPolicy()


    def resolve_policy(overrides: dict | None = None) -> RetryPolicy:
        if not overrides:
            return DEFAULT_POLICY
        fields = {
            "max_attempts": int,
            "base_delay_seconds": float,
            "max_delay_seconds": float,
            "jitter": bool,
        }
        kwargs = {}
        for key, caster in fields.items():
            if key in overrides:
                kwargs[key] = caster(overrides[key])
        return RetryPolicy(**{**DEFAULT_POLICY.__dict__, **kwargs})
    '''
).strip()

_TEST_PY = textwrap.dedent(
    '''
    import pytest

    from checkout.retry import RetryPolicy, resolve_policy


    def test_delay_backs_off_exponentially():
        p = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=10.0)
        assert p.delay_for(1) == 1.0
        assert p.delay_for(2) == 2.0
        assert p.delay_for(3) == 4.0


    def test_delay_is_capped():
        p = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=3.0)
        assert p.delay_for(9) == 3.0


    def test_retry_stops_at_max_attempts():
        p = RetryPolicy(max_attempts=2)
        assert p.should_retry(1, 500) is True
        assert p.should_retry(2, 500) is False


    def test_408_is_retried():
        p = resolve_policy()
        assert p.should_retry(1, 408) is True
    '''
).strip()

_CONFIG_PATH = "src/checkout/retry.py"
_TEST_PATH = "tests/test_retry.py"


def _patched_config() -> str:
    """The same file with 408 added -- a genuine two-token change."""
    return _CONFIG_PY.replace(
        "retry_on_status: tuple[int, ...] = (429, 500, 502, 503, 504)",
        "retry_on_status: tuple[int, ...] = (408, 429, 500, 502, 503, 504)",
    )


def run_demo(audit_root: Path, *, session_id: str | None = None) -> str:
    """Write a synthetic session under `audit_root`; return its session id.

    The shape below is the failure mode the auditor is built to expose:
    the agent re-reads files it has already read (context it is already
    carrying), and rewrites whole files to change one line.

    The demo always writes to a fixed session id, so any events from a
    previous `demo` run are cleared first. Without this, a second run
    appends to the first and every number on the report silently doubles.
    """
    sid = session_id or "demo-session"
    stale = Path(audit_root) / sid
    if stale.is_dir():
        for leftover in ("events.jsonl", "session.json"):
            try:
                (stale / leftover).unlink()
            except FileNotFoundError:
                pass

    session = FrameworkAuditSession(
        framework="demo",
        model=DEMO_MODEL,
        task=DEMO_TASK,
        out_dir=audit_root,
        session_id=sid,
    )

    # Turn 1 -- orient. Reads the test file to see what's failing.
    session.record_tool("list_dir", {"path": "src/checkout"}, "retry.py\nclient.py\n__init__.py")
    session.record_tool("read_file", {"path": _TEST_PATH}, _TEST_PY)
    session.record_llm({"prompt_tokens": 2_180, "completion_tokens": 240}, DEMO_MODEL)

    # Turn 2 -- reads the implementation.
    session.record_tool("read_file", {"path": _CONFIG_PATH}, _CONFIG_PY)
    session.record_llm({"prompt_tokens": 3_450, "completion_tokens": 310}, DEMO_MODEL)

    # Turn 3 -- re-reads the *same* file it already has in context. Pure
    # duplicate spend, and extremely common in real ReAct loops.
    session.record_tool("read_file", {"path": _CONFIG_PATH}, _CONFIG_PY)
    session.record_llm({"prompt_tokens": 4_720, "completion_tokens": 180}, DEMO_MODEL)

    # Turn 4 -- rewrites the entire file to add one status code. Every
    # unchanged line in that payload is billed output tokens.
    session.record_tool(
        "write_file",
        {"path": _CONFIG_PATH, "content": _patched_config()},
        f"wrote {_CONFIG_PATH}",
    )
    session.record_llm({"prompt_tokens": 5_010, "completion_tokens": 1_120}, DEMO_MODEL)

    # Turn 5 -- re-reads it a third time to "verify", then rewrites again
    # after a trivial formatting tweak.
    session.record_tool("read_file", {"path": _CONFIG_PATH}, _patched_config())
    session.record_tool(
        "write_file",
        {"path": _CONFIG_PATH, "content": _patched_config() + "\n"},
        f"wrote {_CONFIG_PATH}",
    )
    session.record_llm({"prompt_tokens": 6_340, "completion_tokens": 1_040}, DEMO_MODEL)

    # Turn 6 -- runs the tests and finishes.
    session.record_tool("run_shell", {"cmd": "pytest tests/test_retry.py -q"}, "4 passed in 0.11s")
    session.record_llm({"prompt_tokens": 6_900, "completion_tokens": 130}, DEMO_MODEL)

    session.finish(success=True)
    return session.session_id
