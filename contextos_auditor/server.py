"""Localhost-only live view for `contextos-auditor watch --serve`.

Deliberately bound to `127.0.0.1` (never `0.0.0.0`) -- this must never be
reachable from anything but the machine it runs on. No external network
call is made anywhere in this module; `AUD-005`'s regression test asserts
that at the process level, not just by code review.

Replaces the old static file's 5-second `<meta http-equiv="refresh">` with
real Server-Sent Events, so the browser tab updates in place instead of a
full-page reload every poll.

LNCH-018: the SSE client patches only the parts of the DOM that actually
changed rather than reassigning `document.body.innerHTML`, which threw away
and rebuilt every node on every poll -- visibly flashing the page, resetting
the scroll position and slamming shut any `<details>` the user had opened to
read a trace. A live dashboard you cannot read while it is live is not a
live dashboard.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from contextos_auditor.report import render_html

#: BUG-C12: hostnames that can only ever resolve to this machine's loopback
#: interface. Anything else in the Host header means the request was
#: addressed to a name we don't own -- i.e. a rebinding attempt.
_ALLOWED_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})

#: LNCH-018: SSE client. Patches the existing DOM in place instead of
#: reassigning `document.body.innerHTML`.
#:
#: `isEqualNode` gives an O(1)-ish subtree short-circuit, so a poll that
#: changes one number touches exactly one text node and leaves the other
#: few thousand alone -- no flash, no scroll jump, no lost selection, and
#: any element the user is interacting with is never re-created.
#:
#: Two deliberate rules:
#:   * `open` on <details> is never synced. Disclosure state belongs to the
#:     reader, not to the server, and re-rendering must not slam shut the
#:     trace they were halfway through reading.
#:   * any failure falls back to the old full-body swap. A dashboard that
#:     flashes is bad; one that silently freezes on stale numbers while the
#:     run continues is a correctness problem.
_LIVE_PATCH_JS = """
<script>
(function () {
  function syncAttrs(oldEl, newEl) {
    var keepOpen = oldEl.nodeName === 'DETAILS';
    for (var i = oldEl.attributes.length - 1; i >= 0; i--) {
      var name = oldEl.attributes[i].name;
      if (keepOpen && name === 'open') continue;
      if (!newEl.hasAttribute(name)) oldEl.removeAttribute(name);
    }
    for (var j = 0; j < newEl.attributes.length; j++) {
      var attr = newEl.attributes[j];
      if (keepOpen && attr.name === 'open') continue;
      if (oldEl.getAttribute(attr.name) !== attr.value) {
        oldEl.setAttribute(attr.name, attr.value);
      }
    }
  }

  function patchNode(oldNode, newNode) {
    if (oldNode.nodeType !== newNode.nodeType ||
        oldNode.nodeName !== newNode.nodeName) {
      oldNode.replaceWith(newNode.cloneNode(true));
      return;
    }
    if (oldNode.nodeType === 3 || oldNode.nodeType === 8) {
      if (oldNode.nodeValue !== newNode.nodeValue) {
        oldNode.nodeValue = newNode.nodeValue;
      }
      return;
    }
    if (oldNode.nodeType !== 1) return;
    if (oldNode.isEqualNode(newNode)) return;
    syncAttrs(oldNode, newNode);
    patchChildren(oldNode, newNode);
  }

  function patchChildren(oldParent, newParent) {
    var oldKids = oldParent.childNodes;
    var newKids = newParent.childNodes;
    var count = newKids.length;
    for (var i = 0; i < count; i++) {
      if (i < oldKids.length) patchNode(oldKids[i], newKids[i]);
      else oldParent.appendChild(newKids[i].cloneNode(true));
    }
    while (oldKids.length > count) oldParent.removeChild(oldParent.lastChild);
  }

  function currentRoot() {
    return document.querySelector('.wrap') || document.body;
  }

  var es = new EventSource('/events');
  es.onmessage = function (event) {
    var fragment = JSON.parse(event.data);
    try {
      var parsed = new DOMParser().parseFromString(
        '<body><div id="__contextos_incoming">' + fragment + '</div></body>',
        'text/html');
      var incoming = parsed.getElementById('__contextos_incoming');
      var nextRoot = incoming.querySelector('.wrap') || incoming;
      // Patch children, never the root element itself, so the node this
      // closure holds stays valid for every later message.
      patchChildren(currentRoot(), nextRoot);
    } catch (err) {
      document.body.innerHTML = fragment;
    }
  };
})();
</script>
"""


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


def _host_header_allowed(host_header: str | None, port: int) -> bool:
    """BUG-C12 (DNS rebinding): the socket is loopback-only, but the server
    used to answer *any* request that reached it regardless of the `Host`
    header. A page the user visits can point `evil.example.com` at
    127.0.0.1 (DNS rebinding) and then read this dashboard cross-origin --
    and the dashboard embeds tool arguments, file paths and file contents.
    Only loopback hostnames addressed at our own port are accepted.

    An absent Host header is allowed: HTTP/1.0 clients and `curl --http1.0`
    legitimately omit it, and a rebinding browser attack always sends one
    (browsers set Host from the URL and cannot forge it)."""
    if host_header is None or host_header.strip() == "":
        return True
    host = host_header.strip()
    # Strip the optional port, handling the bracketed IPv6 form [::1]:8765.
    if host.startswith("["):
        closing = host.find("]")
        hostname = host[1:closing] if closing != -1 else host
        rest = host[closing + 1:] if closing != -1 else ""
        host_port = rest[1:] if rest.startswith(":") else ""
    elif host.count(":") == 1:
        hostname, host_port = host.rsplit(":", 1)
    else:
        hostname, host_port = host, ""
    if hostname.lower() not in _ALLOWED_HOSTNAMES:
        return False
    if host_port and host_port != str(port):
        return False
    return True


def _make_handler(session_dir: Path, poll_interval: float, snapshot_fn):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            pass  # keep stdout to the auditor's own status lines only

        def _host_ok(self) -> bool:
            """BUG-C12: applied to every route, `/` and `/events` alike."""
            port = getattr(getattr(self, "server", None), "server_port", None)
            if port is None:
                port = self.server_address[1] if getattr(self, "server_address", None) else 0
            if _host_header_allowed(self.headers.get("Host"), port):
                return True
            self.send_response(403)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"403 Forbidden: contextos-auditor only answers requests addressed to "
                b"127.0.0.1 or localhost (DNS-rebinding protection).\n"
            )
            return False

        def do_GET(self) -> None:  # noqa: N802
            if not self._host_ok():
                return
            if self.path.startswith("/events"):
                self._serve_events()
            else:
                self._serve_page()

        def _serve_page(self) -> None:
            session_id, meta, result = snapshot_fn(session_dir)
            body = render_html(session_id, meta, result, poll_interval, auto_refresh=False)
            body = body.replace("</body>", _LIVE_PATCH_JS + "</body>")
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
                last_fragment: str | None = None
                while True:
                    session_id, meta, result = snapshot_fn(session_dir)
                    inner = render_html(session_id, meta, result, poll_interval, auto_refresh=False)
                    # Strip the outer <html>/<head>/<body> wrapper -- the
                    # client patches this fragment into the live DOM, so
                    # only the content inside <body>...</body> is useful.
                    start = inner.find("<body>") + len("<body>")
                    end = inner.find("</body>")
                    fragment = inner[start:end].strip()
                    if fragment == last_fragment:
                        # LNCH-018: an idle agent produces an identical
                        # render every poll. Sending it made the client do
                        # a full parse + diff for a guaranteed no-op. A
                        # comment line keeps the connection (and any proxy
                        # idle timer) alive without waking the DOM at all.
                        self.wfile.write(b": heartbeat\n\n")
                    else:
                        payload = json.dumps(fragment)
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        last_fragment = fragment
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
    except (OSError, OverflowError) as e:
        # BUG-C10: an out-of-range port (e.g. --port 99999) raises
        # OverflowError, not OSError, from socket.bind() -- which used to
        # escape as a raw traceback. argparse rejects those at parse time
        # now; this stays as defence-in-depth for programmatic callers.
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
