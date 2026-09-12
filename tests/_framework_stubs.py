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

import contextlib
import enum
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


def ensure_crewai() -> None:
    """`contextos_auditor.crewai` needs the event bus singleton and two
    event classes -- but only inside `attach()`/`detach()`, not at import
    time, which is why its test file's module-level `importorskip` left the
    adapter at **0% executed coverage** while the suite reported green. It
    is the one adapter quoted on the marketing site (43.4%) whose code had
    never been run by a test.

    The stand-in mirrors the real `CrewAIEventsBus` contract, verified
    against upstream `crewai/events/event_bus.py`:
      * `on(EventType)` returns a decorator that registers the handler
      * `off(event_type, handler)` unregisters it (upstream line 368)
      * `emit(source, event=...)` dispatches to handlers for `type(event)`
      * `scoped_handlers()` saves/restores the registry
      * `flush()` is a no-op here

    Upstream stores handlers in a *set* and dispatches them through a
    ThreadPoolExecutor; this stub dispatches synchronously and in order on
    purpose, so adapter-logic tests stay deterministic. The genuinely
    concurrent behaviour is a separate, explicitly threaded test -- see
    BUG-A3.
    """
    if _already_installed("crewai"):
        return

    root = types.ModuleType("crewai")
    # Inside the range compat.py was verified against ([0.100.0, 2.0.0)), so
    # the stub never provokes an unrelated version-drift warning into a
    # test's assertions.
    root.__version__ = "1.0.0"
    root.__path__ = []

    events = types.ModuleType("crewai.events")
    events.__path__ = []
    bus_mod = types.ModuleType("crewai.events.event_bus")
    types_mod = types.ModuleType("crewai.events.types")
    types_mod.__path__ = []
    llm_mod = types.ModuleType("crewai.events.types.llm_events")
    tool_mod = types.ModuleType("crewai.events.types.tool_usage_events")

    class LLMCallCompletedEvent:
        def __init__(self, *, usage=None, model=None, call_id=None, **kw):
            self.usage = usage
            self.model = model
            self.call_id = call_id
            for k, v in kw.items():
                setattr(self, k, v)

    class ToolUsageFinishedEvent:
        def __init__(self, *, tool_name=None, tool_args=None, output=None, **kw):
            self.tool_name = tool_name
            self.tool_args = tool_args
            self.output = output
            for k, v in kw.items():
                setattr(self, k, v)

    class _EventBus:
        def __init__(self) -> None:
            self._handlers: dict[type, list] = {}

        def on(self, event_type):
            def decorator(handler):
                self._handlers.setdefault(event_type, []).append(handler)
                return handler

            return decorator

        def off(self, event_type, handler) -> None:
            handlers = self._handlers.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    del self._handlers[event_type]

        def emit(self, source, event=None) -> None:
            for handler in list(self._handlers.get(type(event), [])):
                handler(source, event)

        def flush(self, timeout=None) -> bool:
            return True

        @contextlib.contextmanager
        def scoped_handlers(self):
            saved = {k: list(v) for k, v in self._handlers.items()}
            try:
                yield
            finally:
                self._handlers = saved

    # `LLMCallType` is part of the same upstream module and is imported by
    # tests/test_model_backfill.py; without it here, that module's
    # `importorskip("crewai")` succeeds against this stub and then dies at
    # import with an ImportError that aborts the whole collection.
    class LLMCallType(str, enum.Enum):
        LLM_CALL = "llm_call"
        TOOL_CALL = "tool_call"

    llm_mod.LLMCallType = LLMCallType
    llm_mod.LLMCallCompletedEvent = LLMCallCompletedEvent
    tool_mod.ToolUsageFinishedEvent = ToolUsageFinishedEvent
    bus_mod.crewai_event_bus = _EventBus()
    bus_mod.CrewAIEventsBus = _EventBus
    types_mod.llm_events = llm_mod
    types_mod.tool_usage_events = tool_mod
    events.event_bus = bus_mod
    events.types = types_mod
    root.events = events

    sys.modules["crewai"] = root
    sys.modules["crewai.events"] = events
    sys.modules["crewai.events.event_bus"] = bus_mod
    sys.modules["crewai.events.types"] = types_mod
    sys.modules["crewai.events.types.llm_events"] = llm_mod
    sys.modules["crewai.events.types.tool_usage_events"] = tool_mod
