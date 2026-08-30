"""Regression test for a real bug found via a live headless-Chrome screenshot:
the server encodes each `/events` SSE payload with `json.dumps(fragment)`
(so it arrives as a JSON *string literal*, quotes/escapes and all), but the
page's client-side JS was assigning that raw string straight into
`document.body.innerHTML` without decoding it first -- corrupting the live
dashboard into visible `\n` / stray quote-mark text after the very first
update. The fix is for the client to `JSON.parse(e.data)` before using it.
"""

from __future__ import annotations

from contextos_auditor.server import _make_handler


def test_served_page_json_parses_sse_payload_before_use() -> None:
    handler_cls = _make_handler(session_dir=None, poll_interval=1.0, snapshot_fn=lambda _d: None)
    # The onmessage script is injected as a literal string constant inside
    # _serve_page -- assert it decodes the JSON payload rather than treating
    # e.data as already-plain HTML.
    import inspect

    source = inspect.getsource(handler_cls._serve_page)
    assert "JSON.parse(e.data)" in source
    assert "innerHTML = e.data" not in source
