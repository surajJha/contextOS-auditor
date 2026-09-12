"""contextos-auditor -- the free Agent Auditor.

Point it at a running CrewAI / LangGraph / AutoGen / OpenAI-Agents-SDK
session with one line, watch real token cost live, get a local report.
No signup. Captures stay local unless you enable OpenTelemetry export.
Your agent's own model-provider calls are unchanged.

Framework hooks live in their own submodules (imported only when you use
them, so an unused framework's SDK is never required):

    from contextos_auditor.crewai import attach          # pip install "contextos-auditor[crewai]"
    from contextos_auditor.langgraph import AuditorCallback  # [langgraph]
    from contextos_auditor.openai_agents import attach    # [openai-agents]
    from contextos_auditor.autogen import new_session, wrap_client, audit_tool  # [autogen]

Then from a terminal: `contextos-auditor watch` (see `contextos-auditor --help`).
"""

from __future__ import annotations

from contextos_auditor._internal.audit_emit import AuditSession, load_events
from contextos_auditor._internal.shadow_kit import shadow_session

__version__ = "0.2.1"

__all__ = ["AuditSession", "load_events", "shadow_session", "__version__"]
