"""Vendored from `spike/tokens.py` in the toku monorepo (source of truth
for the measured benchmark numbers). Copied rather than imported because
this package must install and run standalone, outside the monorepo, for
any external user — see this package's README for the full disclaimer.

tiktoken is an OpenAI tokenizer; used here only as a best-effort token
counter for the Auditor's *local* waste-estimate math (never sent anywhere,
never billed against). If tiktoken isn't installed or its BPE table can't
be fetched, this falls back to a chars/4 estimate — good enough for an
order-of-magnitude "would Kit have helped here" estimate, not precise
enough to publish as a benchmark number (that discipline lives in the
monorepo's copy via `require_exact_tokenizer()`, intentionally not
reproduced here since this package never publishes benchmark claims).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

ENCODING_NAME = "o200k_base"

CACHE_DIR = Path.home() / ".cache" / "contextos-auditor" / "tokcache"

_ENC = None


def _enc():
    global _ENC
    if _ENC is None:
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(CACHE_DIR))
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            import tiktoken

            _ENC = tiktoken.get_encoding(ENCODING_NAME)
        except Exception:
            _ENC = False
    return _ENC


def exact_tokenizer_available() -> bool:
    return _enc() is not False


@functools.lru_cache(maxsize=100_000)
def count_text(text: str) -> int:
    if not text:
        return 0
    e = _enc()
    if e is False:
        return max(1, len(text) // 4)
    return len(e.encode(text, disallowed_special=()))
