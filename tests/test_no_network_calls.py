"""AUD-005: the free Auditor's entire pitch is "no data leaves your
machine" -- this test makes that a CI-enforced guarantee, not a claim that
depends on nobody accidentally adding a network call later.

Monkeypatches `socket.socket.connect`/`connect_ex` for the duration of a
real `watch --serve` cycle (server started in a background thread, hit via
real HTTP requests to `/` and `/events`, same code path a user's browser
takes) and asserts every single outbound connection attempt, without
exception, targets a loopback address.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request

import pytest

from contextos_auditor._internal.audit_emit import AuditSession
from contextos_auditor.cli import snapshot
from contextos_auditor.server import _LoopbackHTTPServer, _make_handler

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture
def recorded_connections(monkeypatch):
    attempts: list[tuple] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _record(addr):
        host = addr[0] if isinstance(addr, tuple) else addr
        attempts.append(host)

    def fake_connect(self, addr, *a, **kw):
        _record(addr)
        return real_connect(self, addr, *a, **kw)

    def fake_connect_ex(self, addr, *a, **kw):
        _record(addr)
        return real_connect_ex(self, addr, *a, **kw)

    monkeypatch.setattr(socket.socket, "connect", fake_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", fake_connect_ex)
    return attempts


def _make_session(tmp_path):
    session = AuditSession(
        tmp_path, session_id="net-iso-test", model="gpt-4o-mini", task="t", framework="crewai"
    )
    session.emit_turn(
        1,
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        cost={"total_nano_aiu": 0},
        tool_calls=[],
    )
    session.finish(success=True)
    return tmp_path / "net-iso-test"


def test_serve_session_makes_no_outbound_network_calls(tmp_path, recorded_connections):
    session_dir = _make_session(tmp_path)
    handler = _make_handler(session_dir, poll_interval=0.2, snapshot_fn=snapshot)
    httpd = _LoopbackHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        # Real HTTP requests over a real socket, same path a user's browser
        # takes -- not a mock. `recorded_connections` above sees every
        # `socket.connect`/`connect_ex` call made anywhere in the process
        # during this block, including ones made by urllib itself.
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as resp:
            assert resp.status == 200

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/events", timeout=3) as resp:
            line = resp.readline().decode("utf-8")
            assert line.startswith("data: ")
            json.loads(line[len("data: "):])  # well-formed SSE payload
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)

    assert recorded_connections, "expected at least the loopback HTTP requests above to be recorded"
    non_loopback = [h for h in recorded_connections if h not in _LOOPBACK_HOSTS]
    assert not non_loopback, f"outbound (non-loopback) connection attempted: {non_loopback}"


def test_loopback_server_binds_to_127_0_0_1_not_all_interfaces(tmp_path):
    session_dir = _make_session(tmp_path)
    handler = _make_handler(session_dir, poll_interval=1.0, snapshot_fn=snapshot)
    httpd = _LoopbackHTTPServer(("127.0.0.1", 0), handler)
    try:
        assert httpd.server_address[0] == "127.0.0.1"
    finally:
        httpd.server_close()


def test_watching_and_reporting_a_session_makes_no_outbound_network_calls(
    tmp_path, recorded_connections
):
    """The default (non `--serve`) `watch`/`report` path -- terminal table +
    static HTML file -- touches only the local filesystem. No server, no
    sockets at all should be opened."""
    from contextos_auditor.report import render_html, render_terminal

    session_dir = _make_session(tmp_path)
    session_id, meta, result = snapshot(session_dir)
    render_terminal(session_id, meta, result)
    render_html(session_id, meta, result, 5.0)

    assert recorded_connections == []
