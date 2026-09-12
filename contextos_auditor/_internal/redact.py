"""Optional secret-pattern redaction for recorded tool args/results
(AUD-016).

Every tool call's args/result_text is written verbatim to local disk by
default (`.contextos/audit/<session>/events.jsonl`) -- necessary for the
write-waste feature (`shadow_kit.py` needs the real old/new file content to
estimate a hunk-based diff), and already never leaves the machine. But a
security-conscious team evaluating this for real adoption will reasonably
ask: "what if a tool call's args/result happens to contain a live API key,
password, or token -- does that get written to disk unredacted?"

This module answers that with an **opt-in, pattern-based scrub**, not a
blanket redaction: it replaces recognizable credential/token-shaped
substrings (AWS keys, Bearer tokens, OpenAI-style `sk-...` keys, generic
`api_key=`/`password=`/`token=` assignments, PEM private key blocks, JWTs)
with a short fixed placeholder, leaving everything else -- including file
content used for waste detection -- intact. This is a deliberate trade-off:
full blanking of read/write_file content would silently break the tool's
core differentiator (real hunk-vs-full-write size comparison), so this
never does that. It is a best-effort scrub for common secret *shapes*, not
a guarantee that no sensitive data of any kind is ever recorded -- say this
plainly in docs/FAQ rather than overclaiming.

Default: **on**. Pass `redact_secrets=False` or set
`CONTEXTOS_REDACT_SECRETS=0` to record raw values instead. This defaults
on because the failure mode of the other default is unrecoverable -- a
credential written into a plaintext file on disk (and possibly committed)
cannot be un-written -- while the cost of it being on is only that a
credential-shaped substring reads as `[REDACTED]` in the trace view.
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = "[REDACTED]"

# Each pattern captures the secret value in group 1 so surrounding text
# (e.g. a key= prefix) is preserved and only the value itself is replaced --
# keeps hunk-size math in shadow_kit.py close to correct instead of
# collapsing a whole line/file to a placeholder.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    # BUG-C2: the old body `[A-Za-z0-9]{20,}` required 20+ *alphanumerics*
    # immediately after `sk-`, so every modern multi-segment key
    # (`sk-proj-...`, `sk-ant-api03-...`) fell straight through and was
    # written verbatim to disk. `-`/`_` are now allowed inside the body.
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # OpenAI/Anthropic-style secret key
    re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"),  # BUG-C2: Stripe keys
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),  # BUG-C2: Google API key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens (ghp_/gho_/ghu_/ghs_/ghr_)
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack tokens
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{10,}"),
    # BUG-C2: connection strings (`postgres://admin:hunter2@db:5432/app`).
    # Only the password group is replaced: the scheme/user/host are useful
    # signal for the trace view and are not themselves credentials.
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]{1,128}:(?P<secret>[^\s/@]{1,256})@"),
    # BUG-C2: the old pattern anchored the key name with `\b`, which can
    # never match inside `aws_secret_access_key` because `_` is itself a
    # word character (no boundary before `secret`). Allowing an arbitrary
    # `[A-Za-z0-9_.-]*` prefix makes prefixed env-var style names match.
    # Only the value (group `secret`) is replaced, so the key name stays
    # readable and hunk-size math in shadow_kit.py stays close to correct.
    re.compile(
        r"(?i)[A-Za-z0-9_.-]*(?:api[_-]?key|secret|passwd|password|token|access[_-]?key)"
        r"\s*[:=]\s*['\"]?(?P<secret>[A-Za-z0-9._~+/=-]{8,})['\"]?"
    ),
    # BUG-C4: the body used to be an unbounded `[\s\S]+?`, which is O(n^2)
    # on input with many BEGIN anchors and no END anchor (measured: 128KB
    # of adversarial text took 1.88s). Redaction runs synchronously in the
    # agent's hot path and `audit_emit` caps result_text at 120,000 chars,
    # so that was reachable. A real PEM private key body is a few KB at
    # most, so bounding the scan at 8,000 chars keeps every genuine key
    # block matching while making the failed-match work per anchor
    # constant instead of proportional to the rest of the document.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,8000}?-----END [A-Z ]*PRIVATE KEY-----"),
]

# BUG-C3: a bounded recursion depth for nested tool args. Agent frameworks
# pass arbitrarily shaped payloads (and occasionally self-referential ones);
# this code runs inside the user's process, so blowing the stack here would
# take their agent run down with it. Past this depth values are passed
# through unredacted rather than risking a crash -- documented as a
# best-effort scrub, consistent with this module's docstring.
_MAX_DEPTH = 8

# PERF: redaction was 55% of all auditor CPU (measured: 1.27s of 2.30s over
# 1500 turns, 99,000 `re.sub` calls). It runs synchronously in the user's
# agent hot path on every tool arg and every tool result, including full
# file bodies, so this is the single most expensive thing the auditor does.
#
# The fix is to avoid running an expensive pattern against text that cannot
# possibly contain it. Every pattern above requires at least one *literal*
# substring to be present, so a cheap literal search is a sound pre-filter:
# if the trigger does not match, the pattern provably cannot match either.
#
# THIS IS SECURITY-CRITICAL. A trigger that is not a true superset of its
# pattern turns into a silently leaked secret, which is far worse than a
# slow auditor. Two rules, enforced by tests in tests/test_redaction.py:
#   1. Each trigger must match *every* string its pattern matches.
#   2. `_TRIGGERS` must stay index-aligned with `_PATTERNS` (asserted below).
# `redact_text_unfiltered` is kept as the reference implementation so the
# tests can assert the fast path is byte-identical to the slow one.
_TRIGGERS: list[re.Pattern[str]] = [
    re.compile(r"AKIA"),
    re.compile(r"sk-"),
    re.compile(r"[spr]k_"),
    re.compile(r"AIza"),
    re.compile(r"gh[pousr]_"),
    re.compile(r"xox"),
    re.compile(r"eyJ"),
    re.compile(r"(?i)bearer"),
    re.compile(r"://"),
    re.compile(r"(?i)key|secret|passw|token"),
    re.compile(r"-----BEGIN "),
]

if len(_TRIGGERS) != len(_PATTERNS):  # pragma: no cover - import-time guard
    raise RuntimeError(
        "redact.py: _TRIGGERS and _PATTERNS are misaligned "
        f"({len(_TRIGGERS)} vs {len(_PATTERNS)}); every pattern needs its own "
        "literal trigger or that pattern would be silently skipped."
    )

# Fast reject for the overwhelmingly common case: a chunk of source code or
# a tool result with nothing secret-shaped in it at all.
#
# Implemented as literal substring tests rather than one big alternation
# regex because `str.__contains__` uses a Crochemore-Perrin scan that is
# several times faster than the regex engine's alternation handling here
# (measured on a 111KB clean file body: 6.61ms for the regex vs 1.69ms for
# this). Case-insensitive triggers are checked against a single lowered
# copy, which is exactly equivalent to a `(?i)` literal match.
#
# Together these are the union of every trigger in _TRIGGERS, so they are a
# superset of the union of every pattern in _PATTERNS.
# `key`/`token` first: they are by far the most common in real code, so the
# suspicious case bails out after one short scan.
_CI_TRIGGERS = ("key", "token", "secret", "passw", "bearer")
_CS_TRIGGERS = (
    "://", "sk-", "AKIA", "AIza", "xox", "eyJ",
    "sk_", "pk_", "rk_",
    "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
    "-----BEGIN ",
)


def _might_contain_secret(text: str) -> bool:
    lowered = text.lower()
    for literal in _CI_TRIGGERS:
        if literal in lowered:
            return True
    for literal in _CS_TRIGGERS:
        if literal in text:
            return True
    return False


def _replace(pattern: re.Pattern[str], text: str) -> str:
    """Replace the `secret` group if the pattern defines one, else the whole
    match. Group-scoped replacement keeps the non-secret context (key name,
    connection host) intact."""
    if "secret" not in pattern.groupindex:
        return pattern.sub(_PLACEHOLDER, text)

    def _sub(m: re.Match[str]) -> str:
        whole, start = m.group(0), m.start(0)
        return (
            whole[: m.start("secret") - start]
            + _PLACEHOLDER
            + whole[m.end("secret") - start :]
        )

    return pattern.sub(_sub, text)


def redact_text(text: str) -> str:
    """Scrub recognizable secret-shaped substrings from `text`. Safe to call
    on arbitrary/empty/non-secret text -- returns it unchanged if nothing
    matches.

    Deterministic by construction (fixed placeholder, no hashing, no
    randomness): two identical file reads must redact to identical strings
    or the duplicate-read detector in shadow_kit.py would stop seeing them
    as duplicates."""
    if not text:
        return text
    # PERF fast path -- see the _TRIGGERS comment above. Semantically a
    # no-op: it only skips patterns that provably cannot match.
    if not _might_contain_secret(text):
        return text
    out = text
    for trigger, pattern in zip(_TRIGGERS, _PATTERNS, strict=True):
        if trigger.search(out):
            out = _replace(pattern, out)
    return out


def redact_text_unfiltered(text: str) -> str:
    """Reference implementation: every pattern, no trigger pre-filtering.

    Kept so the test suite can prove the optimized `redact_text` above is
    byte-identical to the naive version on adversarial corpora. Not used at
    runtime.
    """
    if not text:
        return text
    out = text
    for pattern in _PATTERNS:
        out = _replace(pattern, out)
    return out


def redact_value(value: Any, _depth: int = 0) -> Any:
    """Redact a tool-arg value. Strings are scrubbed; dict/list/tuple
    containers are walked recursively (BUG-C3: secrets nested one level
    down -- e.g. `{"headers": {"Authorization": "Bearer ..."}}` -- used to
    be written to disk verbatim) up to `_MAX_DEPTH`. Container types are
    preserved so the JSON writer sees the same shape it did before."""
    if isinstance(value, str):
        return redact_text(value)
    if _depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {k: redact_value(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v, _depth + 1) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_value(v, _depth + 1) for v in value)
    return value


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    return {k: redact_value(v) for k, v in args.items()}
