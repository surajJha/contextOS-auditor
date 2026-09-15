"""Beginner guidance, noninteractive automation, and safe support-file behavior."""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from contextos_auditor import cli
from contextos_auditor._internal import onboarding


def _report(*, healthy=True):
    return {
        "schema_version": 1,
        "environment": {
            "auditor_version": "0.2.1",
            "python_version": "3.13.0",
            "python_implementation": "CPython",
            "platform": "Linux",
            "in_virtualenv": True,
            "executable": "/private/user/bin/python",
        },
        "checks": [],
        "healthy": healthy,
    }


@pytest.mark.parametrize("framework", ["demo", *onboarding.SNIPPETS])
def test_noninteractive_setup_prints_useful_guide_without_running_anything(
    framework, tmp_path, monkeypatch, capsys,
):
    def no_input(*args):
        pytest.fail("Explicit setup must never prompt")

    monkeypatch.setattr("builtins.input", no_input)
    assert cli.main(["setup", "--framework", framework, "--audit-root", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "contextos_auditor.cli" in text
    assert list(tmp_path.iterdir()) == []
    if framework == "demo":
        assert "synthetic" in text and "--html" in text
    else:
        assert "SAME Python environment" in text
        assert "try:" in text and "finally:" in text
        assert "success=completed" in text
        assert "not standalone agents" in text
        assert "--check-recording" in text
        assert "does not reconfigure your agent" in text


def test_setup_without_selection_in_pipe_exits_without_reading_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("must not read from pipe"))
    assert cli.main(["setup"]) == 2
    assert "--framework" in capsys.readouterr().err


def test_interactive_setup_reprompts_bad_choices(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    values = iter(["invalid", "9" * 5000, "\u00b2", "2"])
    monkeypatch.setattr("builtins.input", lambda *args: next(values))
    assert cli.main(["setup"]) == 0
    text = capsys.readouterr().out
    assert text.count("Choose one of the listed") == 3
    assert "Set up crewai auditing" in text


@pytest.mark.parametrize("cancel", ["0", "q", "eof"])
def test_setup_cancellation_is_safe(cancel, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def answer(*args):
        if cancel == "eof":
            raise EOFError
        return cancel

    monkeypatch.setattr("builtins.input", answer)
    assert cli.main(["setup", "--audit-root", str(tmp_path)]) == 0
    assert "nothing changed" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_setup_ctrl_c_returns_conventional_status(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    assert cli.main(["setup"]) == 130
    assert "Stopped" in capsys.readouterr().out


@pytest.mark.parametrize("topic", onboarding.TOPICS)
def test_recovery_guides_are_actionable(topic, tmp_path, capsys):
    assert cli.main(["troubleshoot", topic, "--framework", "autogen",
                     "--audit-root", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert onboarding.auditor_command(
        "doctor", "--framework", "autogen", "--audit-root", str(tmp_path),
        "--output", "auditor-diagnostics.json",
    ) in text
    assert "--check-recording" in text
    assert "nothing is uploaded" in text
    assert "Do not attach raw recordings" in text


def test_troubleshooting_pipe_lists_topics_without_blocking(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("must not prompt"))
    assert cli.main(["troubleshoot"]) == 0
    text = capsys.readouterr().out
    assert all(f"troubleshoot {topic}" in text for topic in onboarding.TOPICS)


def test_troubleshooting_interactive_selection(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *args: "dashboard")
    assert cli.main(["troubleshoot"]) == 0
    assert "8766" in capsys.readouterr().out


def test_snippets_are_valid_python_with_failure_cleanup():
    for text in onboarding.SNIPPETS.values():
        # OpenAI/AutoGen snippets belong inside the user's async application.
        ast.parse("async def example():\n" + "\n".join("    " + line for line in text.splitlines()))
        assert "completed = False" in text
        assert "finally:" in text


@pytest.mark.parametrize("framework", onboarding.SNIPPETS)
@pytest.mark.parametrize("succeeds", [True, False])
def test_generated_templates_finish_with_actual_run_status(framework, succeeds, monkeypatch):
    finishes = []
    audit = SimpleNamespace(
        detach=lambda **kw: finishes.append(kw["success"]),
        finish=lambda **kw: finishes.append(kw["success"]),
    )

    def run(*args, **kwargs):
        if not succeeds:
            raise RuntimeError("agent failed")
        return "real result"

    async def run_async(*args, **kwargs):
        return run()

    module = ModuleType("template_adapter")
    module.attach = lambda **kw: audit
    module.AuditorCallback = lambda **kw: audit
    module.new_session = lambda **kw: audit
    module.wrap_client = lambda client, session: client
    module.audit_tool = lambda tool, session: tool
    suffix = framework.replace("-", "_")
    monkeypatch.setitem(sys.modules, f"contextos_auditor.{suffix}", module)
    agents = ModuleType("agents")
    agents.Runner = SimpleNamespace(run=run_async)
    monkeypatch.setitem(sys.modules, "agents", agents)
    agentchat = ModuleType("autogen_agentchat.agents")
    agentchat.AssistantAgent = lambda *a, **kw: SimpleNamespace(run=run_async)
    monkeypatch.setitem(sys.modules, "autogen_agentchat", ModuleType("autogen_agentchat"))
    monkeypatch.setitem(sys.modules, "autogen_agentchat.agents", agentchat)
    namespace = {
        "crew": SimpleNamespace(kickoff=run),
        "graph": SimpleNamespace(invoke=run),
        "inputs": {},
        "agent": object(),
        "real_client": object(),
        "your_tool": lambda: "tool result",
    }
    source = "async def example():\n" + "\n".join(
        "    " + line for line in onboarding.SNIPPETS[framework].splitlines()
    )
    exec(compile(source, "<setup-template>", "exec"), namespace)
    if succeeds:
        asyncio.run(namespace["example"]())
    else:
        with pytest.raises(RuntimeError, match="agent failed"):
            asyncio.run(namespace["example"]())
    assert finishes == [succeeds]


def test_windows_commands_quote_powershell_paths(monkeypatch):
    monkeypatch.setattr(onboarding, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(sys, "executable", "C:\\Users\\O'Brien\\Agent Env\\python.exe")
    text = onboarding.python_command("-m", "pip", "install", "contextos-auditor[crewai]")
    assert text.startswith("& 'C:\\Users\\O''Brien\\Agent Env\\python.exe'")
    assert "'contextos-auditor[crewai]'" in text


def test_posix_commands_quote_current_interpreter(monkeypatch):
    monkeypatch.setattr(onboarding, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(sys, "executable", "/tmp/Agent Env/python")
    assert onboarding.python_command("-m", "pip") == "'/tmp/Agent Env/python' -m pip"


def test_doctor_passes_selection_and_selftest_to_collector(tmp_path, monkeypatch, capsys):
    calls = []

    def collect(root, **kwargs):
        calls.append((root, kwargs))
        return _report()

    monkeypatch.setattr(cli, "collect_diagnostics", collect)
    assert cli.main(["doctor", "--framework", "autogen", "--check-recording",
                     "--audit-root", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1
    assert calls == [(tmp_path, {"framework": "autogen", "check_recording": True})]


@pytest.mark.parametrize("strict,healthy,expected", [
    (False, False, 0), (True, False, 1), (True, True, 0),
])
def test_doctor_strict_exit_codes(strict, healthy, expected, monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect_diagnostics", lambda *a, **k: _report(healthy=healthy))
    args = ["doctor", "--json"] + (["--strict"] if strict else [])
    assert cli.main(args) == expected
    assert json.loads(capsys.readouterr().out)["healthy"] is healthy


def test_informational_doctor_still_clearly_reports_failed_checks(monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect_diagnostics", lambda *a, **k: _report(healthy=False))
    assert cli.main(["doctor"]) == 0
    assert "Required checks failed" in capsys.readouterr().out


def test_support_file_and_stdout_are_same_safe_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect_diagnostics", lambda *a, **k: _report())
    output = tmp_path / "diagnostics.json"
    assert cli.main(["doctor", "--json", "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(captured.out)
    assert "/private/user" not in captured.out
    assert "nothing uploaded" in captured.err


def test_support_file_never_overwrites_existing_data(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect_diagnostics", lambda *a, **k: _report())
    output = tmp_path / "events.jsonl"
    output.write_text("existing recording", encoding="utf-8")
    assert cli.main(["doctor", "--output", str(output)]) == 1
    assert output.read_text(encoding="utf-8") == "existing recording"
    assert "Choose a new output filename" in capsys.readouterr().err


def test_missing_support_output_parent_is_visible_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect_diagnostics", lambda *a, **k: _report())
    assert cli.main(["doctor", "--output", str(tmp_path / "missing" / "report.json")]) == 1
    assert "FileNotFoundError" in capsys.readouterr().err


@pytest.mark.parametrize("args", [
    ["--debug", "doctor"], ["doctor", "--debug"],
])
def test_debug_flag_exposes_cli_traceback_on_either_side(args, monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("diagnostic failure")

    monkeypatch.setattr(cli, "collect_diagnostics", fail)
    with pytest.raises(RuntimeError, match="diagnostic failure"):
        cli.main(args)


def test_default_cli_failure_gives_recovery_without_traceback(monkeypatch, capsys):
    monkeypatch.delenv("CONTEXTOS_AUDITOR_TRACEBACK", raising=False)

    def fail(*a, **k):
        raise RuntimeError("diagnostic failure")

    monkeypatch.setattr(cli, "collect_diagnostics", fail)
    assert cli.main(["doctor"]) == 1
    text = capsys.readouterr().err
    assert "troubleshoot crash" in text and "--debug" in text
    assert "Traceback (most recent call last)" not in text


@pytest.mark.parametrize("serve", [False, True])
def test_demo_points_to_setup_without_unsafe_shell_cleanup(serve, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "serve_session", lambda *a, **kw: 0)
    args = ["demo", "--audit-root", str(tmp_path)] + (["--serve"] if serve else [])
    assert cli.main(args) == 0
    text = capsys.readouterr().out
    assert "contextos-auditor setup" in text
    assert "rm -rf" not in text


def test_waiting_viewer_points_to_updated_setup_and_recovery(tmp_path, monkeypatch, capsys):
    sessions = iter([None, tmp_path])
    monkeypatch.setattr(cli, "_resolve_session_dir", lambda args: next(sessions))
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    args = cli.build_parser().parse_args(["watch", "--audit-root", str(tmp_path)])
    assert cli._wait_for_session_dir(args, poll_seconds=30) == tmp_path
    text = capsys.readouterr().out
    assert "contextos-auditor setup" in text
    assert "troubleshoot no-data" in text
    assert "exact snippet" not in text
