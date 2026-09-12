"""Test-suite wiring for contextos-auditor.

Two jobs, both about keeping test output trustworthy.

1. **Drain OpenTelemetry before pytest prints its summary.** Several tests
   deliberately point an exporter at a dead collector to prove that an
   unreachable OTLP endpoint can never break the agent's run. Those spans
   are flushed by an `atexit` hook, i.e. *after* pytest has written its
   summary line, so the exporter's "Transient error ... retrying" messages
   used to overwrite the one line a human reads to decide whether the suite
   passed. Draining in `pytest_sessionfinish` moves that noise above the
   summary instead of on top of it.

2. **Reset per-process global state between tests.** `base.py` keeps
   module-level `_warned_labels` and `_announced` flags so a given warning
   fires exactly once per process. That is right in production and wrong in
   a test suite, where it makes a test's warning assertions depend on which
   tests ran before it -- the classic pass-alone/fail-in-suite trap.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _reset_once_per_process_state(monkeypatch):
    """Give every test the module-level state a fresh process would have."""
    from contextos_auditor._internal import base, otel_export, tokens

    base._warned_labels.clear()
    base._announced = False
    otel_export._otel_warned = False
    # Fake/fallback encoders must not leave digest-cached counts or select
    # a different package's tokenizer cache for later monorepo tests.
    monkeypatch.setattr(tokens, "_ENC", None)
    monkeypatch.setenv(
        "TIKTOKEN_CACHE_DIR",
        os.environ.get("TIKTOKEN_CACHE_DIR", str(tokens.CACHE_DIR)),
    )
    tokens.cache_clear()
    yield
    tokens.cache_clear()
    base._warned_labels.clear()
    base._announced = False


def pytest_sessionfinish(session, exitstatus):
    """Flush and shut down any OTel providers while pytest still owns the
    terminal, so the summary line stays the last thing on screen."""
    try:
        from contextos_auditor._internal.otel_export import _shutdown_all

        _shutdown_all()
    except Exception:  # noqa: BLE001 -- teardown must never fail the run
        pass
