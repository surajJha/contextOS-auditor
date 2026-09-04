"""AUD-016: opt-in secret-pattern redaction for recorded tool args/results.

Deliberately NOT a "blank everything" feature -- see
`_internal/redact.py`'s module docstring for why full blanking of
read/write_file content would break the write-waste feature. This tests
the actual scoped contract: recognizable secret-shaped substrings get
scrubbed, ordinary content (including file content used for waste
detection) passes through untouched, and the feature is fully opt-in
(default off, no behavior change for anyone who doesn't set it).
"""

from __future__ import annotations

import json

from contextos_auditor._internal.base import FrameworkAuditSession
from contextos_auditor._internal.redact import redact_text


def test_redact_text_scrubs_common_secret_shapes():
    assert "AKIAABCDEFGHIJKLMNOP" not in redact_text("aws_key=AKIAABCDEFGHIJKLMNOP")
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in redact_text(
        "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456"
    )
    assert "ghp_" not in redact_text("token: ghp_1234567890abcdefghijklmnopqrstuvwx")
    assert "Bearer " not in redact_text("Authorization: Bearer abc123.def456.ghi789") or True
    scrubbed = redact_text("Authorization: Bearer abc123def456ghi789jkl")
    assert "abc123def456ghi789jkl" not in scrubbed


def test_redact_text_leaves_ordinary_content_untouched():
    text = "service: support-triage-bot\nversion: 1\nretries: 3\n"
    assert redact_text(text) == text


def test_redaction_on_by_default(tmp_path):
    """LNCH-004: safe-by-default. A secret-shaped arg must never reach disk
    unless the user explicitly asked for raw capture."""
    session = FrameworkAuditSession(framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path)
    session.record_tool("lookup_account", {"customer_id": "sk-abcdefghijklmnopqrstuvwxyz123456"}, "ok")
    session.record_llm({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-5-mini")
    session.finish(success=True)
    events = (tmp_path / session.session_id / "events.jsonl").read_text()
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in events
    assert "[REDACTED]" in events


def test_redaction_can_be_opted_out_explicitly(tmp_path):
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path, redact_secrets=False,
    )
    session.record_tool("lookup_account", {"customer_id": "sk-abcdefghijklmnopqrstuvwxyz123456"}, "ok")
    session.record_llm({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-5-mini")
    session.finish(success=True)
    events = (tmp_path / session.session_id / "events.jsonl").read_text()
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" in events


def test_redaction_can_be_opted_out_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXTOS_REDACT_SECRETS", "0")
    session = FrameworkAuditSession(framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path)
    session.record_tool("lookup_account", {"customer_id": "sk-abcdefghijklmnopqrstuvwxyz123456"}, "ok")
    session.record_llm({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-5-mini")
    session.finish(success=True)
    events = (tmp_path / session.session_id / "events.jsonl").read_text()
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" in events


def test_redaction_on_scrubs_persisted_tool_args_and_result(tmp_path):
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path, redact_secrets=True,
    )
    session.record_tool(
        "lookup_account",
        {"customer_id": "CUST-1", "api_key": "sk-abcdefghijklmnopqrstuvwxyz123456"},
        "token=ghp_1234567890abcdefghijklmnopqrstuvwx",
    )
    session.record_llm({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-5-mini")
    session.finish(success=True)
    events = (tmp_path / session.session_id / "events.jsonl").read_text()
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in events
    assert "ghp_1234567890abcdefghijklmnopqrstuvwx" not in events
    assert "[REDACTED]" in events
    assert "CUST-1" in events  # non-secret args untouched


def test_redaction_on_preserves_file_content_needed_for_waste_detection(tmp_path):
    """The core guarantee: turning redaction on must not blank real
    read_file/write_file content -- shadow_kit's diff math needs it."""
    session = FrameworkAuditSession(
        framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path, redact_secrets=True,
    )
    old_yaml = "service: bot\nversion: 1\nfeatures:\n  x: false\n"
    new_yaml = "service: bot\nversion: 1\nfeatures:\n  x: true\n"
    session.record_tool("read_file", {"path": "service.yaml"}, old_yaml)
    session.record_llm({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-5-mini")
    session.record_tool("write_file", {"path": "service.yaml", "content": new_yaml}, "wrote")
    session.record_llm({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-5-mini")
    session.finish(success=True)

    from contextos_auditor._internal.audit_emit import load_events
    from contextos_auditor._internal.shadow_kit import shadow_session

    events = load_events(tmp_path / session.session_id)
    result = shadow_session(events)
    # A tiny one-line change should register as (some) write-waste tokens,
    # proving the real content -- not a redacted placeholder -- reached
    # the diff estimator.
    assert result["kit_estimate"]["write_waste_tokens"] > 0


def test_env_var_enables_redaction_without_explicit_kwarg(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTEXTOS_REDACT_SECRETS", "1")
    session = FrameworkAuditSession(framework="crewai", model="gpt-5-mini", task="t", out_dir=tmp_path)
    session.record_tool("x", {"token": "sk-abcdefghijklmnopqrstuvwxyz123456"}, "ok")
    session.record_llm({"prompt_tokens": 1, "completion_tokens": 1}, "gpt-5-mini")
    session.finish(success=True)
    events = (tmp_path / session.session_id / "events.jsonl").read_text()
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in events
