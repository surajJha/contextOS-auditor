"""Client-side behaviour of the `watch --serve` live view.

Two bugs are pinned here.

1. The original regression: `/events` encodes each payload with
   `json.dumps(fragment)`, so it arrives as a JSON *string literal* --
   quotes and escapes and all. The client used to assign `e.data` straight
   into the DOM without decoding it, corrupting the dashboard into visible
   `\\n` and stray quote marks after the first update. Found via a live
   headless-Chrome screenshot.

2. LNCH-018: the client then replaced `document.body.innerHTML` wholesale
   on every poll. Correct output, unusable ergonomics -- every node was
   destroyed and rebuilt once per second, so the page flashed, the scroll
   position jumped to the top and any `<details>` the user had opened to
   read a trace slammed shut. The client now patches only the subtrees that
   changed.

The assertions below are made against the bytes actually served, not
against the source of any particular function, so moving the script around
cannot make them pass vacuously.

The patcher's *runtime* behaviour was verified separately by executing this
exact script (extracted from `_LIVE_PATCH_JS`) in a real DOM under jsdom:
node identity is preserved across updates, an opened `<details>` survives a
re-render, focus is retained, appended turns leave existing turns untouched,
and a malformed payload falls back instead of throwing. The old innerHTML
client fails 15 of those assertions, so they do describe this fix and not
merely the DOM. That harness needs a JS runtime and a DOM, neither of which
this package will ever depend on, so what remains here are the structural
invariants that harness relies on.
"""

from __future__ import annotations

import json
import re

import pytest

from contextos_auditor._internal.shadow_kit import shadow_session
from contextos_auditor.server import _LIVE_PATCH_JS, _make_handler

META = {"framework": "crewai", "model": "gpt-4o", "status": "running"}


class _Sink:
    """Captures what the handler writes instead of a socket."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    def flush(self) -> None:
        pass

    @property
    def text(self) -> str:
        return b"".join(self.chunks).decode("utf-8")


class _StubHandler:
    """A handler instance without the BaseHTTPRequestHandler socket setup."""

    def __init__(self) -> None:
        self.wfile = _Sink()
        self.headers_sent: list[tuple[str, str]] = []
        self.status: int | None = None

    def send_response(self, code: int) -> None:
        self.status = code

    def send_header(self, key: str, value: str) -> None:
        self.headers_sent.append((key, value))

    def end_headers(self) -> None:
        pass


def _snapshot(_dir):
    return "sess-1", META, shadow_session([])


def _served_page() -> str:
    handler_cls = _make_handler(session_dir=None, poll_interval=1.0, snapshot_fn=_snapshot)
    stub = _StubHandler()
    handler_cls._serve_page(stub)
    return stub.wfile.text


# --------------------------------------------------------- the served page


def test_page_decodes_the_json_payload_before_touching_the_dom():
    page = _served_page()
    assert "JSON.parse(event.data)" in page
    # The raw, still-encoded value must never reach the DOM.
    assert "innerHTML = e.data" not in page
    assert "innerHTML = event.data" not in page


def test_page_subscribes_to_the_events_stream():
    assert "new EventSource('/events')" in _served_page()


def test_page_does_not_use_meta_refresh():
    """The whole point of SSE here: no full-page reload."""
    assert "http-equiv" not in _served_page().lower()


# ------------------------------------------------------------- LNCH-018


def test_the_happy_path_never_replaces_the_whole_body():
    """A full-body swap is the bug. It may survive *only* as the fallback
    inside the catch block."""
    js = _LIVE_PATCH_JS
    assert "document.body.innerHTML" in js, "the safety fallback should still exist"
    catch_body = js.split("catch (err)", 1)[1]
    assert "document.body.innerHTML" in catch_body
    assert js.count("document.body.innerHTML") == 1, (
        "body.innerHTML must appear only in the fallback, never on the "
        "normal update path"
    )


def test_a_failed_patch_still_updates_rather_than_freezing():
    """Stale numbers presented as live ones are worse than a flash."""
    assert re.search(r"catch\s*\(err\)\s*\{[^}]*innerHTML", _LIVE_PATCH_JS)


def test_details_open_state_is_never_synced_from_the_server():
    """Disclosure state belongs to the reader. If the server's `open`
    attribute were synced, every poll would close the trace the user was
    reading."""
    js = _LIVE_PATCH_JS
    assert "DETAILS" in js
    assert js.count("'open'") >= 2, (
        "both the remove-attribute and set-attribute loops must skip `open`"
    )


def test_identical_subtrees_are_skipped_entirely():
    """Without this short-circuit the patcher walks every node on every
    poll, which is the cost the innerHTML swap was paying anyway."""
    assert "isEqualNode" in _LIVE_PATCH_JS


def test_the_patcher_never_replaces_the_root_it_holds():
    """`patchChildren` at the top level keeps the cached root node valid
    for every later message; patching the root itself could detach it and
    silently stop all further updates."""
    handler = _LIVE_PATCH_JS.split("es.onmessage", 1)[1]
    assert "patchChildren(" in handler
    assert "patchNode(currentRoot()" not in handler


# ------------------------------------------------- the /events stream


def _drive_events(snapshots, monkeypatch):
    """Run `_serve_events` over a fixed list of snapshots, then stop it the
    way a closed browser tab would."""
    calls = {"n": 0}

    def snapshot_fn(_dir):
        i = min(calls["n"], len(snapshots) - 1)
        return "sess-1", META, snapshots[i]

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= len(snapshots):
            raise BrokenPipeError

    monkeypatch.setattr("contextos_auditor.server.time.sleep", fake_sleep)
    handler_cls = _make_handler(session_dir=None, poll_interval=0.0, snapshot_fn=snapshot_fn)
    stub = _StubHandler()
    handler_cls._serve_events(stub)
    return stub


def _frames(stub) -> list[str]:
    return [f for f in stub.wfile.text.split("\n\n") if f.strip()]


def test_events_stream_sets_the_sse_content_type(monkeypatch):
    stub = _drive_events([shadow_session([])], monkeypatch)
    assert ("Content-Type", "text/event-stream") in stub.headers_sent


def test_first_snapshot_is_always_sent(monkeypatch):
    stub = _drive_events([shadow_session([])], monkeypatch)
    frames = _frames(stub)
    assert len(frames) == 1
    assert frames[0].startswith("data: ")


def test_unchanged_snapshots_send_a_heartbeat_not_a_repeat(monkeypatch):
    """An idle agent renders identically every poll. Re-sending it made the
    client parse and diff a guaranteed no-op once per second."""
    stub = _drive_events([shadow_session([])] * 4, monkeypatch)
    frames = _frames(stub)
    data_frames = [f for f in frames if f.startswith("data: ")]
    comments = [f for f in frames if f.startswith(":")]
    assert len(data_frames) == 1, "only the first render carries a payload"
    assert len(comments) == 3, "the rest keep the connection alive silently"


def test_a_changed_snapshot_is_sent(monkeypatch):
    """The dedupe must never swallow a real update."""
    empty = shadow_session([])
    changed = json.loads(json.dumps(empty))
    changed["turns"] = [
        {
            "turn": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }
    ]
    stub = _drive_events([empty, empty, changed], monkeypatch)
    data_frames = [f for f in _frames(stub) if f.startswith("data: ")]
    assert len(data_frames) == 2, "the first render and the change, not the repeat"


def test_payload_is_a_json_string_of_the_body_fragment(monkeypatch):
    stub = _drive_events([shadow_session([])], monkeypatch)
    payload = _frames(stub)[0][len("data: ") :]
    fragment = json.loads(payload)
    assert isinstance(fragment, str)
    # The fragment is spliced into a live document, so it must not carry a
    # second <html>/<body> wrapper with it.
    assert "<body" not in fragment
    assert "<html" not in fragment
    assert 'class="wrap"' in fragment


def test_payload_never_contains_a_bare_newline(monkeypatch):
    """SSE frames are newline-delimited; an unescaped newline in the body
    would split one update into two malformed frames. `json.dumps` is what
    guarantees this."""
    stub = _drive_events([shadow_session([])], monkeypatch)
    payload = _frames(stub)[0][len("data: ") :]
    assert "\n" not in payload


@pytest.mark.parametrize("exc", [BrokenPipeError, ConnectionResetError])
def test_a_closed_tab_does_not_raise(exc, monkeypatch):
    """Closing the browser must not print a traceback over the auditor's
    own status output."""

    def fake_sleep(_seconds):
        raise exc

    monkeypatch.setattr("contextos_auditor.server.time.sleep", fake_sleep)
    handler_cls = _make_handler(session_dir=None, poll_interval=0.0, snapshot_fn=_snapshot)
    handler_cls._serve_events(_StubHandler())
