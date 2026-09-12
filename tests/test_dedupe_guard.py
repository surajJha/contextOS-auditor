"""Coverage and correctness for DedupeGuard (BUG-B6 / BUG-B7).

This module sat at 36% branch coverage while carrying two real fixes: a
signature that used to embed memory addresses (so structurally identical
calls never deduped) and two maps that grew without bound for the life of
the process.

Both failure modes are silent. A broken signature does not raise, it just
quietly stops detecting duplicates -- which is the entire product.
"""

from __future__ import annotations

import threading


from contextos_auditor._internal import dedupe
from contextos_auditor._internal.dedupe import DedupeGuard, _signature


# ------------------------------------------------------------------ B6


def test_identical_calls_share_a_signature():
    assert _signature("read_file", {"path": "a.py"}) == _signature(
        "read_file", {"path": "a.py"}
    )


def test_signature_is_independent_of_key_order():
    """Nested dicts used to compare by insertion order, so the same call
    built two different ways looked like two different calls."""
    a = _signature("grep", {"pattern": "x", "path": "a.py", "opts": {"i": True, "n": 1}})
    b = _signature("grep", {"path": "a.py", "opts": {"n": 1, "i": True}, "pattern": "x"})
    assert a == b


def test_signature_does_not_embed_memory_addresses():
    """`repr()` of a plain object contains its address, so two structurally
    identical calls produced different signatures on every run -- and so
    never deduplicated. Agent frameworks pass exactly these (Pydantic
    models, dataclasses, custom arg objects), so this was not an edge case."""

    class Payload:
        def __init__(self, v):
            self.v = v

    s1 = _signature("tool", {"arg": Payload(1)})
    s2 = _signature("tool", {"arg": Payload(1)})
    assert "0x" not in s1[1], s1
    # The property that actually matters: same structure -> same signature.
    assert s1 == s2
    # ...and genuinely different payloads must still differ.
    assert s1 != _signature("tool", {"arg": Payload(2)})


def test_signature_of_slotted_objects_is_stable():
    """`vars()` raises on __slots__ classes, so this exercises the repr
    fallback rather than the structural path."""

    class Slotted:
        __slots__ = ("v",)

        def __init__(self, v):
            self.v = v

    s1 = _signature("tool", {"arg": Slotted(1)})
    s2 = _signature("tool", {"arg": Slotted(1)})
    assert "0x" not in s1[1], s1
    assert s1 == s2


def test_signature_of_nested_objects_is_order_independent():
    class Cfg:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    a = _signature("tool", {"cfg": Cfg(x=1, y=2)})
    b = _signature("tool", {"cfg": Cfg(y=2, x=1)})
    assert a == b


def test_signature_of_different_calls_differs():
    assert _signature("read_file", {"path": "a.py"}) != _signature(
        "read_file", {"path": "b.py"}
    )
    assert _signature("read_file", {"path": "a.py"}) != _signature(
        "write_file", {"path": "a.py"}
    )


def test_signature_survives_unserialisable_and_self_referential_args():
    """Must degrade, never raise -- this runs inside the user's agent."""
    cyclic: dict = {"self": None}
    cyclic["self"] = cyclic
    assert _signature("tool", cyclic)

    class Boom:
        def __repr__(self):
            return "<Boom>"

    assert _signature("tool", {"x": Boom()})


# ------------------------------------------------------------------ check/bump


def test_repeat_call_within_an_epoch_is_a_duplicate():
    g = DedupeGuard()
    assert g.check("read_file", {"path": "a.py"}) is False
    assert g.check("read_file", {"path": "a.py"}) is True
    assert g.check("read_file", {"path": "a.py"}) is True


def test_bump_starts_a_new_epoch_and_clears_duplicate_status():
    g = DedupeGuard()
    g.check("read_file", {"path": "a.py"})
    assert g.check("read_file", {"path": "a.py"}) is True
    g.bump()
    assert g.check("read_file", {"path": "a.py"}) is False


def test_distinct_calls_are_never_duplicates():
    g = DedupeGuard()
    assert g.check("read_file", {"path": "a.py"}) is False
    assert g.check("read_file", {"path": "b.py"}) is False


# ------------------------------------------------------------------ check_write / TTL


def test_write_repeat_inside_the_ttl_is_a_duplicate():
    g = DedupeGuard()
    args = {"path": "a.py", "content": "x"}
    assert g.check_write("write_file", args) is False
    assert g.check_write("write_file", args) is True


def test_write_repeat_after_the_ttl_is_allowed_again():
    g = DedupeGuard()
    args = {"path": "a.py", "content": "x"}
    assert g.check_write("write_file", args, ttl=0.0) is False
    assert g.check_write("write_file", args, ttl=0.0) is False


def test_duplicate_write_does_not_extend_its_own_ttl():
    """If a duplicate refreshed the timestamp, a hot loop could keep a write
    suppressed indefinitely."""
    g = DedupeGuard()
    args = {"path": "a.py", "content": "x"}
    assert g.check_write("write_file", args, ttl=100.0) is False
    first = dict(g._write_seen)
    assert g.check_write("write_file", args, ttl=100.0) is True
    assert dict(g._write_seen) == first


# ------------------------------------------------------------------ B7 bounds


def test_read_signatures_do_not_grow_without_bound(monkeypatch):
    monkeypatch.setattr(dedupe, "_MAX_TRACKED", 50)
    g = DedupeGuard()
    for i in range(500):
        g.check("read_file", {"path": f"f{i}.py"})
    assert len(g._seen) <= 50


def test_write_signatures_do_not_grow_without_bound(monkeypatch):
    monkeypatch.setattr(dedupe, "_MAX_TRACKED", 50)
    g = DedupeGuard()
    for i in range(500):
        g.check_write("write_file", {"path": f"f{i}.py", "content": "x" * 100})
    assert len(g._write_seen) <= 50


def test_eviction_is_least_recently_used_not_arbitrary(monkeypatch):
    """The entry we keep touching is the one most likely to repeat, so it
    must be the last thing evicted."""
    monkeypatch.setattr(dedupe, "_MAX_TRACKED", 10)
    g = DedupeGuard()
    hot = {"path": "hot.py"}
    g.check("read_file", hot)
    for i in range(9):
        g.check("read_file", {"path": f"cold{i}.py"})
        g.check("read_file", hot)  # keep it warm
    for i in range(50):
        g.check("read_file", {"path": f"flood{i}.py"})
        g.check("read_file", hot)
    assert g.check("read_file", hot) is True, "the hottest key was evicted"


def test_expired_write_entries_are_purged(monkeypatch):
    monkeypatch.setattr(dedupe, "_MAX_TRACKED", 10)
    g = DedupeGuard()
    for i in range(100):
        g.check_write("write_file", {"path": f"f{i}.py"}, ttl=0.0)
    assert len(g._write_seen) <= 10


# ------------------------------------------------------------------ concurrency


def test_concurrent_checks_do_not_corrupt_state_or_raise():
    g = DedupeGuard()
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        try:
            barrier.wait()
            for i in range(200):
                g.check("read_file", {"path": f"{n}-{i % 20}.py"})
                g.check_write("write_file", {"path": f"{n}-{i % 20}.py"})
                if i % 50 == 0:
                    g.bump()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(g._seen) <= dedupe._MAX_TRACKED
    assert len(g._write_seen) <= dedupe._MAX_TRACKED


def test_exactly_one_of_two_identical_concurrent_writes_is_allowed():
    """The point of check_write is that a racing duplicate is suppressed."""
    g = DedupeGuard()
    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(16)

    def worker() -> None:
        barrier.wait()
        r = g.check_write("write_file", {"path": "same.py", "content": "x"}, ttl=60.0)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(False) == 1, f"expected one winner, got {results.count(False)}"
    assert results.count(True) == 15


def test_write_lifecycle_supports_optimiser_guard_contract(tmp_path):
    g = DedupeGuard()
    path = str(tmp_path / "a.py")
    assert g.ambiguous_failures_for(path) == 0
    assert g.note_ambiguous_edit_failure(path) == 1
    assert g.note_ambiguous_edit_failure(path) == g.HARD_BLOCK_AFTER_AMBIGUOUS_FAILURES
    g.reset_ambiguous_failures(path)
    assert g.ambiguous_failures_for(path) == 0
    g.note_successful_write(path, "write succeeded")
    g.bump()
    assert g.check_post_write_read(str(tmp_path / "." / "a.py")) == "write succeeded"
    assert g.check_post_write_read(path) is None


def test_post_write_window_counts_guarded_calls_not_elapsed_time(tmp_path):
    g = DedupeGuard()
    path = str(tmp_path / "a.py")
    g.note_successful_write(path, "write succeeded")
    for index in range(g.POST_WRITE_READ_WINDOW_CALLS + 1):
        g.check("read_file", {"path": f"other-{index}.py"})
    assert g.check_post_write_read(path) is None
    assert not g._post_write


def test_additional_guard_state_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(dedupe, "_MAX_TRACKED", 10)
    g = DedupeGuard()
    for index in range(100):
        path = str(tmp_path / f"{index}.py")
        g.note_ambiguous_edit_failure(path)
        g.note_successful_write(path, "write succeeded")
    assert len(g._ambiguous_fail_count) <= 10
    assert len(g._post_write) <= 10


def test_ambiguous_failure_counts_are_thread_safe():
    g = DedupeGuard()
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(100):
            g.note_ambiguous_edit_failure("a.py")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert g.ambiguous_failures_for("a.py") == 800
