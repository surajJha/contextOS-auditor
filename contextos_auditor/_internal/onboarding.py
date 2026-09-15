"""Local setup and recovery guides; never install packages or run user agents."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path


def python_command(*args: str) -> str:
    """Use the current interpreter, including paths with spaces."""
    parts = [sys.executable, *args]
    if os.name == "nt":
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in parts)
    return " ".join(shlex.quote(part) for part in parts)


def auditor_command(*args: str) -> str:
    return python_command("-m", "contextos_auditor.cli", *args)


SNIPPETS = {
    "crewai": '''from contextos_auditor.crewai import attach

# Use your existing configured crew.
audit = attach(task="Describe your task")
completed = False
try:
    result = crew.kickoff()
    completed = True
finally:
    audit.detach(success=completed)''',
    "langgraph": '''from contextos_auditor.langgraph import AuditorCallback

# Use your existing graph and inputs. Keep any other callbacks you need.
handler = AuditorCallback(task="Describe your task")
completed = False
try:
    result = graph.invoke(inputs, config={"callbacks": [handler]})
    completed = True
finally:
    handler.finish(success=completed)''',
    "openai-agents": '''from agents import Runner
from contextos_auditor.openai_agents import attach

# Inside your existing async function; keep SDK tracing enabled.
# Replace YOUR_MODEL_NAME with the exact model configured on your agent.
audit = attach(task="Describe your task", model_hint="YOUR_MODEL_NAME")
completed = False
try:
    result = await Runner.run(agent, "Your existing task")
    completed = True
finally:
    audit.detach(success=completed)''',
    "autogen": '''from autogen_agentchat.agents import AssistantAgent
from contextos_auditor.autogen import new_session, wrap_client, audit_tool

# Inside your existing async function; use your configured real_client.
session = new_session(task="Describe your task", model="YOUR_MODEL_NAME")
client = wrap_client(real_client, session)
# Preserve your agent's other settings; wrap each tool you want to audit.
agent = AssistantAgent("assistant", model_client=client,
                       tools=[audit_tool(your_tool, session)])
completed = False
try:
    result = await agent.run(task="Your existing task")
    completed = True
finally:
    session.finish(success=completed)
# Close your model client using your application's existing cleanup.''',
}

FRAMEWORK_NOTES = {
    "crewai": (
        "Current CrewAI releases need Python below 3.14. "
        "Keep one active audit run per process; use separate workers for overlapping runs."
    ),
    "langgraph": (
        "The [langgraph] extra installs the callback dependency, not the full "
        "LangGraph application or your model provider. Pass the handler to each run."
    ),
    "openai-agents": (
        "Disabled SDK tracing means no capture. Sensitive-data tracing settings "
        "can also remove tool details. Keep one active audit run per process."
    ),
    "autogen": (
        "Use AutoGen 0.4+, not legacy pyautogen. The [autogen] extra installs "
        "autogen-core; install agentchat and your provider separately. "
        "Wrap both the client and tools; use the exact configured model for pricing."
    ),
}


def setup_guide(framework: str, audit_root: Path) -> str:
    if framework == "demo":
        return (
            "Try the demo first (synthetic data, no API key or model calls):\n\n"
            f"  {auditor_command('demo', '--audit-root', str(audit_root), '--serve')}\n\n"
            "No browser on this machine? Write a portable HTML file instead:\n\n"
            f"  {auditor_command('demo', '--audit-root', str(audit_root), '--html', 'demo.html')}\n\n"
            "The demo is not connected to your agent. Run setup again and select "
            "your framework to record real usage."
        )
    if framework not in SNIPPETS:
        raise ValueError(f"Unknown framework: {framework}")
    root = str(audit_root)
    return (
        f"Set up {framework} auditing\n\n"
        "1. Use the SAME Python environment that runs your agent.\n"
        f"   Current interpreter: {sys.executable}\n"
        "   If this is the wrong environment, activate your agent's environment first.\n"
        "   Nothing below is executed automatically. Windows commands use PowerShell.\n\n"
        "2. Check that environment and the local recorder:\n"
        f"   {auditor_command('doctor', '--framework', framework, '--audit-root', root, '--check-recording')}\n"
        "   If the SDK is missing, install only the extra you need:\n"
        f"   {python_command('-m', 'pip', 'install', f'contextos-auditor[{framework}]')}\n\n"
        "3. Add this around your EXISTING agent run, then run your application.\n"
        "   These are integration templates, not standalone agents. Replace the\n"
        "   existing-agent variables and model placeholders with your own values.\n"
        "   Frameworks still need their normal model credentials; Auditor needs none.\n\n"
        f"{SNIPPETS[framework]}\n\n"
        f"   Important: {FRAMEWORK_NOTES[framework]}\n"
        "   For streams, consume the stream fully before finishing the audit.\n\n"
        "4. Open the viewer from the SAME working directory as your agent:\n"
        f"   {auditor_command('watch', '--serve')}\n"
        "   If your agent runs elsewhere, point the viewer at its actual audit directory:\n"
        f"   {auditor_command('watch', '--audit-root', root, '--serve')}\n"
        "   --audit-root selects what the viewer reads; it does not reconfigure your agent.\n"
        "   Success means a real session appears and turns increase as your agent runs.\n"
        "   A passing synthetic self-test alone does not prove the SDK is attached.\n\n"
        "No data or something looks wrong?\n"
        f"   {auditor_command('troubleshoot', 'no-data', '--framework', framework)}\n"
        "Token counts depend on SDK usage data. Costs and savings opportunities are estimates."
    )


TOPICS = {
    "install": "Installation or command not found",
    "no-data": "Agent runs, but no session or turns appear",
    "wrong-totals": "Token counts or costs look wrong",
    "dashboard": "Browser dashboard does not open",
    "crash": "Command or recording fails",
}


def troubleshooting_guide(topic: str, framework: str | None, audit_root: Path) -> str:
    if topic not in TOPICS:
        raise ValueError(f"Unknown troubleshooting topic: {topic}")
    framework_args = ("--framework", framework) if framework else ()
    doctor_args = ("doctor", *framework_args, "--audit-root", str(audit_root))
    checks = {
        "install": (
            "Install Auditor into your agent's existing virtual environment, not a separate pipx viewer.\n"
            "Check which Python is running and use that interpreter for pip:\n"
            f"  {python_command('-m', 'pip', 'show', 'contextos-auditor')}\n"
            f"  {python_command('-m', 'pip', 'check')}\n"
            "If the console command is not on PATH, use the module command below.\n"
            "For externally-managed-environment errors, create/activate a virtual environment;\n"
            "do not bypass system Python protections. Current CrewAI requires Python <3.14.\n"
            "If Auditor cannot install at all, use the Installation section of the public README."
        ),
        "no-data": (
            "Check that the adapter is attached INSIDE the process running your agent.\n"
            "The viewer and pipx do not attach an adapter automatically; the demo is synthetic.\n"
            f"Viewer currently looks under: {audit_root.resolve()}\n"
            "Compare this with your agent's working directory and recording out_dir.\n"
            "No session: check attachment and directory. Session but no turns: check callbacks,\n"
            "SDK tracing, client wrapping, and whether the model actually returned usage.\n"
            "Finish/detach after the run; fully consume streams before closing the audit.\n"
            "A recorder self-test cannot prove your application's hooks are wired correctly."
        ),
        "wrong-totals": (
            "First compare one small real run with the usage returned by your provider SDK.\n"
            "Zero counts can mean missing SDK usage, disabled tracing, or an unfinished stream.\n"
            "Use the exact configured model name for pricing; model families can be ambiguous.\n"
            "Tool-text token estimates are not provider billing counts. Savings are opportunities,\n"
            "not money already saved. Missing tool capture makes the opportunity estimate incomplete.\n"
            "Do not overlap independent CrewAI/OpenAI audit scopes in the same process.\n"
            "Keep each session's callbacks/client wrappers separate and inspect capture warnings."
        ),
        "dashboard": (
            "Open the exact http://127.0.0.1:PORT URL printed by the viewer.\n"
            "If the port is busy, choose another, for example:\n"
            f"  {auditor_command('watch', '--audit-root', str(audit_root), '--serve', '--port', '8766')}\n"
            "SSH: forward the remote port (ssh -L 8765:127.0.0.1:8765 user@host).\n"
            "Docker: localhost is inside the container; port publishing alone is not enough.\n"
            "Use a mounted audit directory and run the viewer on the host, or export HTML:\n"
            f"  {auditor_command('report', '--audit-root', str(audit_root), '--html', 'report.html')}\n"
            "The viewer waits if no session exists; that is different from a browser failure."
        ),
        "crash": (
            "Run the local recorder self-test first. Check free disk space and write permissions.\n"
            "To see a CLI traceback, add --debug to the command that fails, for example:\n"
            f"  {auditor_command('report', '--audit-root', str(audit_root), '--debug')}\n"
            "For repeated capture warnings, set this BEFORE running your agent (in its process):\n"
            '  import os; os.environ["CONTEXTOS_AUDITOR_VERBOSE"] = "1"\n'
            "CLI --debug affects this CLI process, not a separately running agent.\n"
            "Capture warnings can mean observations were dropped; check totals before using them."
        ),
    }
    return (
        f"{TOPICS[topic]}\n\n{checks[topic]}\n\n"
        "Check the selected environment and a temporary synthetic recording:\n"
        f"  {auditor_command(*doctor_args, '--check-recording')}\n\n"
        "Still stuck? Write a minimal support report (nothing is uploaded):\n"
        f"  {auditor_command(*doctor_args, '--output', 'auditor-diagnostics.json')}\n"
        "Review it before attaching it to https://github.com/surajJha/contextOS-auditor/issues\n"
        "Do not attach raw recordings, HTML reports, prompts, credentials, or debug tracebacks without review."
    )
