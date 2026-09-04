"""LNCH-008: adapter tests must keep running on a bare `pip install pytest`.

contextos-auditor ships with zero runtime dependencies and every framework
SDK is an optional extra, so making crewai/langchain-core/autogen-core/
openai-agents test dependencies would quietly turn a zero-dependency
package into a four-SDK one and let a broken adapter ship green on any
machine where the SDK simply isn't installed (`importorskip` skips, CI goes
green, the user's run is still broken).

Instead each adapter module's *import-time* SDK requirement is satisfied by
the smallest stand-in that has the right shape, and only when the real SDK
is absent -- so on a developer machine that does have the SDK installed the
tests exercise the genuine base classes, and on a bare machine they still
run. The behaviour under test is driven entirely by fake event/span/usage
objects either way, so no test outcome depends on which of the two is
present.
"""

from __future__ import annotations

import importlib.util
import sys
import types


def _already_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def ensure_langchain_core() -> None:
    """`contextos_auditor.langgraph` needs `langchain_core.callbacks.
    BaseCallbackHandler` at import time and nothing else."""
    if _already_installed("langchain_core"):
        return

    root = types.ModuleType("langchain_core")
    # Inside the range compat.py was verified against, so the stub never
    # provokes an unrelated version-drift warning into a test's assertions.
    root.__version__ = "0.3.0"
    root.__path__ = []  # marks it a package so `langchain_core.callbacks` imports

    callbacks = types.ModuleType("langchain_core.callbacks")

    class BaseCallbackHandler:
        """langchain_core's real handler base is a plain object with
        `ignore_*` properties the dispatcher consults; adapters only ever
        subclass it, so an empty base is behaviourally identical here."""

    callbacks.BaseCallbackHandler = BaseCallbackHandler
    root.callbacks = callbacks
    sys.modules["langchain_core"] = root
    sys.modules["langchain_core.callbacks"] = callbacks


def ensure_autogen_core() -> None:
    """`contextos_auditor.autogen` subclasses `autogen_core.models.
    ChatCompletionClient`."""
    if _already_installed("autogen_core"):
        return

    root = types.ModuleType("autogen_core")
    root.__version__ = "0.4.0"
    root.__path__ = []

    models = types.ModuleType("autogen_core.models")

    class ChatCompletionClient:
        """Real base is an ABC; the adapter implements every abstract
        method, so a non-abstract stand-in accepts the same subclass."""

    models.ChatCompletionClient = ChatCompletionClient
    root.models = models
    sys.modules["autogen_core"] = root
    sys.modules["autogen_core.models"] = models
