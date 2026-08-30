"""AUD-012: opt-in OTel GenAI-semconv span export.

Two things must hold regardless of whether the optional `opentelemetry-*`
packages are installed in this test environment:
  1. `otel_endpoint=None` (the default for every existing caller) must not
     construct an exporter at all -- zero behavior change for anyone not
     opting in.
  2. Whether or not the real packages are importable, `FrameworkAuditSession`
     must keep recording locally (the JSONL trail) exactly as before --
     OTel export is strictly additive and must never be able to break it.
"""

from __future__ import annotations

import json

from contextos_auditor._internal.base import FrameworkAuditSession
from contextos_auditor._internal.otel_export import OtelExporter, build_exporter


def test_no_endpoint_means_no_exporter_at_all():
    assert build_exporter(None, framework="crewai") is None
    assert build_exporter("", framework="crewai") is None


def test_session_without_otel_endpoint_has_no_exporter(tmp_path):
    session = FrameworkAuditSession(framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path)
    assert session._otel is None


def test_session_with_otel_endpoint_gets_a_disabled_or_enabled_exporter(tmp_path):
    # Whether or not opentelemetry is installed in this environment, this
    # must not raise and must not be None -- an OtelExporter is always
    # constructed once an endpoint is configured, it just may be `enabled`
    # False if the optional packages aren't importable.
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path,
        otel_endpoint="http://127.0.0.1:4318/v1/traces",
    )
    assert isinstance(session._otel, OtelExporter)


def test_local_recording_unaffected_by_otel_endpoint_being_set(tmp_path):
    """The core guarantee: turning on OTel export must not change a single
    byte of the always-on local JSONL recording."""
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path,
        otel_endpoint="http://127.0.0.1:4318/v1/traces",
    )
    session.record_tool("read_file", {"path": "x.yaml"}, "contents")
    session.record_llm({"prompt_tokens": 10, "completion_tokens": 5}, "gpt-5-mini")
    session.finish(success=True)

    meta = json.loads((tmp_path / session.session_id / "session.json").read_text())
    assert meta["model"] == "gpt-5-mini"
    events = (tmp_path / session.session_id / "events.jsonl").read_text().strip().splitlines()
    assert len(events) == 1
    turn = json.loads(events[0])
    assert turn["usage"]["prompt_tokens"] == 10
    assert turn["tool_calls"][0]["name"] == "read_file"


def test_env_var_enables_otel_without_explicit_kwarg(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXTOS_OTEL_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
    session = FrameworkAuditSession(framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path)
    assert session._otel is not None


def test_explicit_kwarg_wins_over_missing_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTEXTOS_OTEL_ENDPOINT", raising=False)
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path,
        otel_endpoint="http://127.0.0.1:4318/v1/traces",
    )
    assert session._otel is not None


def test_exporter_never_raises_even_if_span_creation_fails(tmp_path):
    """A broken/unreachable OTLP endpoint (real production scenario: the
    collector isn't running) must never surface as an exception to the
    agent's real run -- record_llm/finish must complete normally."""
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path,
        otel_endpoint="http://127.0.0.1:1/v1/traces",  # nothing listens here
    )
    session.record_llm({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-5-mini")
    session.finish(success=True)  # must not raise
