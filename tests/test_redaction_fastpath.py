"""PERF fast-path equivalence: the trigger pre-filter must never change output.

`redact_text` skips a pattern when its cheap literal trigger does not match.
That is only sound if every trigger is a true *superset* of its pattern. If
one is not, a real secret is written to disk in plaintext -- a security bug
that no amount of speed justifies.

These tests are the proof. They compare the optimized `redact_text` against
`redact_text_unfiltered` (every pattern, no skipping) on curated secrets,
adversarial text, and fuzzed input.
"""

from __future__ import annotations

import random
import string

import pytest

from contextos_auditor._internal import redact
from contextos_auditor._internal.redact import (
    _PATTERNS,
    _TRIGGERS,
    redact_text,
    redact_text_unfiltered,
)

# One string per pattern, in the same order as _PATTERNS.
REAL_SECRETS = [
    "AKIAIOSFODNN7EXAMPLE",
    "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
    "sk_live_abcdefghijklmnop1234",
    "AIzaabcdefghijklmnopqrstuvwxyzABCDEFGHI",  # AIza + exactly 35 chars
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "xoxb-1234567890-abcdefghij",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk",
    "Authorization: Bearer abcdef1234567890",
    "postgres://app:sup3rs3cr3t@db.internal:5432/app",
    "aws_secret_access_key = wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
]


def test_every_pattern_has_an_index_aligned_trigger():
    assert len(_TRIGGERS) == len(_PATTERNS)


@pytest.mark.parametrize("secret", REAL_SECRETS, ids=lambda s: s[:18])
def test_global_prefilter_admits_every_real_secret(secret):
    """The cheap literal prefilter runs before anything else; if it rejects
    a text, no pattern ever runs. It must therefore be a superset of the
    union of all patterns."""
    assert redact._might_contain_secret(secret), (
        f"prefilter rejects {secret[:24]!r} -- that secret would be written "
        "to disk verbatim"
    )
    assert redact._might_contain_secret(f"lots of\nunrelated code\n{secret}\nmore code\n")


@pytest.mark.parametrize("secret", REAL_SECRETS, ids=lambda s: s[:18])
def test_each_real_secret_is_still_redacted_through_the_fast_path(secret):
    text = f"some preceding code\nvalue = {secret}\ntrailing code\n"
    out = redact_text(text)
    assert secret not in out, "fast path let a real secret through"
    assert out == redact_text_unfiltered(text)


def test_trigger_matches_everything_its_own_pattern_matches():
    """The superset property, checked per index rather than in aggregate --
    an aggregate check would pass even if trigger[i] only matched because
    some *other* pattern's literal happened to be present."""
    for i, (trigger, pattern, sample) in enumerate(
        zip(_TRIGGERS, _PATTERNS, REAL_SECRETS, strict=True)
    ):
        assert pattern.search(sample), f"sample {i} does not exercise its pattern"
        assert trigger.search(sample), (
            f"trigger[{i}] ({trigger.pattern!r}) misses text that pattern[{i}] "
            f"({pattern.pattern!r}) matches -- this secret would leak"
        )


def test_all_secrets_in_one_blob_match_the_reference_implementation():
    blob = "\n".join(REAL_SECRETS)
    out = redact_text(blob)
    assert out == redact_text_unfiltered(blob)
    for secret in REAL_SECRETS:
        assert secret not in out


def test_secret_embedded_in_a_large_innocuous_file_is_still_caught():
    """The fast path exists for big file bodies; make sure a needle deep
    inside a 200KB haystack is not what gets skipped."""
    filler = "def f(x):\n    return x + 1\n" * 8000
    text = filler + "\nOPENAI_KEY = sk-proj-abcdefghijklmnopqrstuvwxyz0123456789\n" + filler
    out = redact_text(text)
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789" not in out
    assert out == redact_text_unfiltered(text)


def test_clean_source_code_is_returned_unchanged():
    code = "import os\n\n\ndef main():\n    return os.getcwd()\n"
    assert redact_text(code) == code
    assert redact_text(code) == redact_text_unfiltered(code)


def test_text_that_trips_a_trigger_but_holds_no_secret_is_unchanged():
    """`key`, `://` and `token` are extremely common in ordinary code; the
    pre-filter will fire on them, and the real patterns must then decline."""
    for noisy in (
        "for key in mapping:\n    print(key)\n",
        "url = 'https://example.com/docs'\n",
        "# tokenize the input stream\ntokens = lex(src)\n",
        "secretary = Employee()\n",
        "passwd_file = '/etc/passwd'\n",
    ):
        assert redact_text(noisy) == noisy, noisy
        assert redact_text(noisy) == redact_text_unfiltered(noisy)


def test_fuzzed_input_never_diverges_from_the_reference():
    rng = random.Random(1337)
    alphabet = string.ascii_letters + string.digits + "-_=:/@.'\" \n{}[]"
    fragments = [
        "sk-", "AKIA", "AIza", "ghp_", "xox", "eyJ", "Bearer ", "://",
        "api_key=", "secret:", "token = ", "-----BEGIN RSA PRIVATE KEY-----",
        "password", "pk_test_", "-----END RSA PRIVATE KEY-----",
    ]
    for _ in range(3000):
        parts = []
        for _ in range(rng.randint(1, 8)):
            if rng.random() < 0.55:
                parts.append(rng.choice(fragments))
            parts.append(
                "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
            )
        text = "".join(parts)
        assert redact_text(text) == redact_text_unfiltered(text), repr(text)


def test_fast_path_is_actually_faster_on_clean_text():
    """Guards against the optimization being silently removed later."""
    import time

    clean = "def handler(request):\n    return 200\n" * 4000
    t0 = time.perf_counter()
    for _ in range(20):
        redact_text(clean)
    fast = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(20):
        redact_text_unfiltered(clean)
    slow = time.perf_counter() - t0
    assert fast < slow, f"fast path ({fast:.4f}s) is not faster than naive ({slow:.4f}s)"


def test_redaction_is_still_deterministic():
    """shadow_kit's duplicate-read detection compares redacted strings; two
    identical reads must redact identically or duplicates stop being seen."""
    text = "key = sk-proj-abcdefghijklmnopqrstuvwxyz0123456789\n"
    assert redact_text(text) == redact_text(text)
    assert redact.redact_text(text) == redact_text_unfiltered(text)
