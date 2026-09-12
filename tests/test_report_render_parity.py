"""Byte-level regression for terminal, HTML and history, with qualified estimates."""

import hashlib

from contextos_auditor import report
from contextos_auditor._internal import tokens
from contextos_auditor._internal.shadow_kit import shadow_session


def test_extracted_report_units_preserve_rendered_output(monkeypatch):
    monkeypatch.setattr(tokens, "_ENC", False)
    result = shadow_session([
        {
            "kind": "turn", "turn": turn,
            "usage": {"prompt_tokens": 200, "completion_tokens": 20},
            "tool_calls": [{
                "name": "read_file", "args": {"path": "a.py"},
                "result_text": "hello\n" * 10,
            }],
        }
        for turn in (1, 2)
    ])
    meta = {"model": "gpt-4o", "framework": "test", "status": "finished"}
    outputs = [
        (
            report.render_terminal("snapshot", meta, result),
            "468b55fa29cab43d3d64c5a910d923cb211f642e31647ae8cb34329528ec3ec4",
        ),
        (
            report.render_html("snapshot", meta, result, 1, auto_refresh=False),
            "e255f2128b7b2759090547b1936051f47104ece2a2fb57570866d57a2be132e3",
        ),
        (
            report.render_history_html([], [], 0, ".contextos/audit"),
            "80ca5c074abd5167131e87aa2cb5834c7ee8c17bb8c61e128409a1ee385b5cbb",
        ),
    ]
    for output, digest in outputs:
        assert hashlib.sha256(output.encode()).hexdigest() == digest
