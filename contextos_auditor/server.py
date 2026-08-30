"""Localhost-only live view for `contextos-auditor watch --serve`.

Deliberately bound to `127.0.0.1` (never `0.0.0.0`) -- this must never be
reachable from anything but the machine it runs on. No external network
call is made anywhere in this module; `AUD-005`'s regression test asserts
that at the process level, not just by code review.

Replaces the old static file's 5-second `<meta http-equiv="refresh">` with
real Server-Sent Events, so the browser tab updates in place instead of a
full-page reload every poll.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from contextos_auditor.report import render_html


class _LoopbackHTTPServer(ThreadingHTTPServer):
    """`http.server.HTTPServer.server_bind()` calls `socket.getfqdn(host)`
    for its `server_name` -- a reverse DNS lookup that can hang for a long
    time (observed: indefinitely) in network-restricted sandboxes/containers
    with no resolver configured for it. Since this server only ever binds
    to `127.0.0.1` and is never addressed by hostname, skip that lookup
    entirely rather than let a loopback-only local tool's startup depend on
    DNS resolution working at all."""

    allow_reuse_address = True

    def server_bind(self) -> None:
        # Deliberately reimplements HTTPServer.server_bind() minus the
        # socket.getfqdn() call, not TCPServer.server_bind() plus a patch --
        # keeps the exact same socket setup, just without the DNS step.
        import socketserver

        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def _make_handler(session_dir: Path, poll_interval: float, snapshot_fn):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            pass  # keep stdout to the auditor's own status lines only

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/events"):
                self._serve_events()
            else:
                self._serve_page()

        def _serve_page(self) -> None:
            session_id, meta, result = snapshot_fn(session_dir)
            body = render_html(session_id, meta, result, poll_interval, live=True)
            body = body.replace(
                "</body>",
                "<script>\n"
                "const es = new EventSource('/events');\n"
                "es.onmessage = (e) => { document.body.innerHTML = e.data; };\n"
                "</script>\n</body>",
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def _serve_events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    session_id, meta, result = snapshot_fn(session_dir)
                    inner = render_html(session_id, meta, result, poll_interval, live=True)
                    # Strip the outer <html>/<head>/<body> wrapper -- the
                    # client replaces document.body.innerHTML with this, so
                    # only the fragment inside <body>...</body> is useful.
                    start = inner.find("<body>") + len("<body>")
                    end = inner.find("</body>")
                    fragment = inner[start:end].strip()
                    payload = json.dumps(fragment)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(poll_interval)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def serve_session(session_dir: Path, *, poll_interval: float, port: int) -> int:
    from contextos_auditor.cli import snapshot

    handler = _make_handler(session_dir, poll_interval, snapshot)
    try:
        httpd = _LoopbackHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        print(f"Could not bind 127.0.0.1:{port} ({e}). Falling back to --once terminal output.")
        from contextos_auditor.report import render_terminal

        session_id, meta, result = snapshot(session_dir)
        print(render_terminal(session_id, meta, result))
        return 1

    print(f"contextos-auditor live view -- http://127.0.0.1:{port} (loopback-only, Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0
