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


def test_c8_token_counting_makes_no_outbound_network_calls(tmp_path, recorded_connections, monkeypatch):
    """BUG-C8(a): `tiktoken.get_encoding()` fetches its BPE table over
    HTTPS on first use. That must never happen implicitly in a package
    that promises nothing leaves the machine."""
    from contextos_auditor._internal import tokens

    monkeypatch.setattr(tokens, "_ENC", None)
    monkeypatch.setattr(tokens, "CACHE_DIR", tmp_path / "tokcache")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "tokcache"))
    monkeypatch.delenv(tokens.DOWNLOAD_ENV, raising=False)

    assert tokens.count_text("some text to count") > 0
    assert tokens.exact_tokenizer_available() is False
    assert recorded_connections == []


def test_c8_report_labels_counts_as_approximate_when_tokenizer_missing(tmp_path, monkeypatch):
    """BUG-C8(b): `exact_tokenizer_available()` was never wired into any
    output, so heuristic counts were presented as if exact."""
    from contextos_auditor import report
    from contextos_auditor._internal import tokens

    monkeypatch.setattr(tokens, "_ENC", False)

    session_dir = _make_session(tmp_path)
    session_id, meta, result = snapshot(session_dir)
    terminal = report.render_terminal(session_id, meta, result)
    html_out = report.render_html(session_id, meta, result, 5.0)

    assert report.TOKEN_ACCURACY_MARKER in terminal
    assert "approximate" in terminal
    assert report.TOKEN_ACCURACY_MARKER in html_out
    assert "chars/4" in html_out


def test_c8_report_names_tokenizer_without_claiming_provider_exactness(tmp_path, monkeypatch):
    from contextos_auditor import report
    from contextos_auditor._internal import tokens

    class _FakeEnc:
        def encode(self, text, disallowed_special=()):
            return list(range(max(1, len(text) // 3)))

    monkeypatch.setattr(tokens, "_ENC", _FakeEnc())

    session_dir = _make_session(tmp_path)
    session_id, meta, result = snapshot(session_dir)
    for output in (report.render_terminal(session_id, meta, result),
                   report.render_html(session_id, meta, result, 5.0)):
        assert "o200k_base" in output
        assert "other models may tokenize differently" in output


@pytest.mark.parametrize("filename", ["unrelated-file", "expected-table"])
def test_unrelated_or_corrupt_cache_never_authorizes_download(tmp_path, monkeypatch, filename):
    import hashlib
    import sys
    from types import SimpleNamespace
    from contextos_auditor._internal import tokens

    cache = tmp_path / "cache"
    cache.mkdir()
    if filename == "expected-table":
        filename = hashlib.sha1(tokens._BPE_URL.encode(), usedforsecurity=False).hexdigest()
    (cache / filename).write_bytes(b"not the expected tokenizer table")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache))
    monkeypatch.delenv(tokens.DOWNLOAD_ENV, raising=False)

    def unexpected_load(*args):
        pytest.fail("uncached encoder could attempt a download")

    monkeypatch.setitem(sys.modules, "tiktoken", SimpleNamespace(get_encoding=unexpected_load))
    assert tokens.exact_tokenizer_available() is False


def test_verified_table_uses_configured_cache_directory(tmp_path, monkeypatch):
    import hashlib
    from contextos_auditor._internal import tokens

    cache = tmp_path / "custom-cache"
    cache.mkdir()
    data = b"verified test table"
    filename = hashlib.sha1(tokens._BPE_URL.encode(), usedforsecurity=False).hexdigest()
    (cache / filename).write_bytes(data)
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(cache))
    monkeypatch.setattr(tokens, "_BPE_SHA256", hashlib.sha256(data).hexdigest())
    assert tokens._bpe_table_is_cached() is True


def test_c8_count_cache_does_not_retain_full_text_bodies():
    """BUG-C8: the old lru_cache(100_000) kept whole 120KB file bodies
    alive for the process lifetime."""
    from contextos_auditor._internal import tokens

    tokens.cache_clear()
    body = "x" * 120_000
    tokens.count_text(body)
    assert body not in tokens._COUNT_CACHE
    assert all(len(k) == 16 for k in tokens._COUNT_CACHE)
    assert tokens.count_text(body) == tokens.count_text(body)  # cache hit path
