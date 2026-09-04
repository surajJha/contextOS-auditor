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
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style secret key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens (ghp_/gho_/ghu_/ghs_/ghr_)
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack tokens
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{10,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token|access[_-]?key)\b\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{8,}['\"]?"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
]


def redact_text(text: str) -> str:
    """Scrub recognizable secret-shaped substrings from `text`. Safe to call
    on arbitrary/empty/non-secret text -- returns it unchanged if nothing
    matches."""
    if not text:
        return text
    out = text
    for pattern in _PATTERNS:
        out = pattern.sub(_PLACEHOLDER, out)
    return out


def redact_value(value: Any) -> Any:
    """Redact a single tool-arg value if it's a string; passes anything
    else (numbers, dicts, lists, None) through unchanged -- callers redact
    dict values one at a time, not by trying to serialize arbitrary
    structures."""
    return redact_text(value) if isinstance(value, str) else value


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    return {k: redact_value(v) for k, v in args.items()}
