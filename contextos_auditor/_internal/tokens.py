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

import hashlib
import os
from pathlib import Path

ENCODING_NAME = "o200k_base"
_BPE_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
_BPE_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"

CACHE_DIR = Path.home() / ".cache" / "contextos-auditor" / "tokcache"

# BUG-C8(a): `tiktoken.get_encoding()` downloads the BPE table over HTTPS
# from openaipublic.blob.core.windows.net on first use. In a package whose
# entire pitch is "nothing leaves your machine", a surprise outbound
# request is not an acceptable default -- and when it fails (offline box,
# corporate TLS interception) every headline number silently became
# `len(text)//4`. So the download is now opt-in: the exact tokenizer is
# used when its table is already cached locally, or when the user
# explicitly allows the one-time fetch via this env var. Otherwise we stay
# offline and *say* the counts are approximate (see `accuracy_note()`).
DOWNLOAD_ENV = "CONTEXTOS_TIKTOKEN_DOWNLOAD"

APPROXIMATE_NOTE = (
    "approximate local tool-text counts (chars/4); provider usage is separate. "
    "Optional contextos-auditor[tiktoken] also needs a cached o200k_base table "
    "or an explicitly permitted download (CONTEXTOS_TIKTOKEN_DOWNLOAD=1)."
)
TOKENIZER_NOTE = (
    "o200k_base counts for local tool text; provider usage is separate "
    "and other models may tokenize differently."
)

_ENC = None


def _download_allowed() -> bool:
    return os.environ.get(DOWNLOAD_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _bpe_table_is_cached() -> bool:
    """Match the exact table and integrity check used by tiktoken.

    An unrelated or corrupt cache file must not authorize a network fetch.
    """
    cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR", str(CACHE_DIR))
    if not cache_dir:
        return False
    cache_key = hashlib.sha1(_BPE_URL.encode(), usedforsecurity=False).hexdigest()
    try:
        data = (Path(cache_dir) / cache_key).read_bytes()
    except OSError:
        return False
    return hashlib.sha256(data).hexdigest() == _BPE_SHA256


def _enc():
    global _ENC
    if _ENC is None:
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(CACHE_DIR))
        try:
            cache_dir = os.environ["TIKTOKEN_CACHE_DIR"]
            if cache_dir:
                Path(cache_dir).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not (_bpe_table_is_cached() or _download_allowed()):
            _ENC = False
            return _ENC
        try:
            import tiktoken

            _ENC = tiktoken.get_encoding(ENCODING_NAME)
        except Exception:
            # Never raises into the caller: this sits behind every recorded
            # tool call in the user's agent process.
            _ENC = False
    return _ENC


def exact_tokenizer_available() -> bool:
    """Whether token counts are real BPE counts (True) or the chars/4
    heuristic (False). BUG-C8(b): this existed but was never called, so the
    user had no way to tell which of the two they were looking at. It is
    now wired into both the terminal report and the HTML dashboard."""
    try:
        return _enc() is not False
    except Exception:
        return False


def accuracy_note() -> str | None:
    """Distinguish local text counting from provider-reported token usage."""
    return TOKENIZER_NOTE if exact_tokenizer_available() else APPROXIMATE_NOTE


# BUG-C8: the old `@lru_cache(maxsize=100_000)` on `count_text` kept up to
# 100k *full text bodies* (audit_emit caps them at 120,000 chars) alive for
# the whole process -- ~GBs in a long agent run, inside the user's own
# process. Keying the cache on a 16-byte digest instead means the entries
# cost a fixed ~50 bytes regardless of input size, so the cache can stay
# large enough to still serve the repeated-content case it exists for
# (duplicate-read detection re-counts the same file body many times).
_COUNT_CACHE: dict[bytes, int] = {}
_COUNT_CACHE_MAX = 20_000


def _count_uncached(text: str) -> int:
    e = _enc()
    if e is False:
        return max(1, len(text) // 4)
    try:
        return len(e.encode(text, disallowed_special=()))
    except Exception:
        return max(1, len(text) // 4)


def count_text(text: str) -> int:
    if not text:
        return 0
    key = hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=16).digest()
    hit = _COUNT_CACHE.get(key)
    if hit is not None:
        return hit
    value = _count_uncached(text)
    if len(_COUNT_CACHE) >= _COUNT_CACHE_MAX:
        # Simple bounded eviction: drop everything rather than track LRU
        # order. Token counting is cheap relative to an agent turn, so a
        # rare cold cache costs far less than the bookkeeping would.
        _COUNT_CACHE.clear()
    _COUNT_CACHE[key] = value
    return value


def cache_clear() -> None:
    _COUNT_CACHE.clear()


count_text.cache_clear = cache_clear  # type: ignore[attr-defined]
