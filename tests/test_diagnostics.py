"""Onboarding diagnostics stay useful without SDKs, real sessions, or network."""

from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from contextos_auditor._internal import diagnostics as d

_REAL_PROBE_SDK = d._probe_sdk


@pytest.fixture(autouse=True)
def sdk_imports(monkeypatch):
    """Every framework import is a stub; never initialize real SDK plugins."""
    configured = {}
    calls = []

    def import_module(name):
        calls.append(name)
        result = configured.get(name, ModuleNotFoundError("Optional SDK missing", name=name))
        if isinstance(result, BaseException):
            raise result
        return result

    # Exercise the actual child boundary code with stub imports for fast unit
    # tests. Dedicated subprocess tests below verify isolation and deadlines.
    namespace = {"__name__": "diagnostics_test_worker"}
    exec(d._SDK_PROBE_CODE, namespace)
    monkeypatch.setattr(d, "_probe_sdk", lambda name: namespace["inspect_sdk"](import_module, name))
    monkeypatch.delenv("OPENAI_AGENTS_DISABLE_TRACING", raising=False)
    return configured, calls


def check(report, check_id):
    return next(row for row in report["checks"] if row["id"] == check_id)


def healthy_sdk(sdk_imports, framework="langgraph", version="0.4.0"):
    configured, _ = sdk_imports
    configured[d.FRAMEWORKS[framework][0]] = SimpleNamespace(__version__=version)


def test_framework_mapping_matches_existing_public_names():
    assert d.FRAMEWORKS == {
        "crewai": ("crewai", "crewai", "crewai"),
        "langgraph": ("langchain_core", "langgraph", "langgraph"),
        "openai-agents": ("agents", "openai-agents", "openai_agents"),
        "autogen": ("autogen_core", "autogen", "autogen"),
    }


def test_fresh_install_all_missing_is_healthy_and_does_not_create_root(tmp_path, sdk_imports):
    root = tmp_path / "missing" / "audit"
    report = d.collect_diagnostics(root)
    assert report["schema_version"] == 1
    assert report["healthy"] is True
    assert set(sdk_imports[1]) == {spec[0] for spec in d.FRAMEWORKS.values()}
    assert all(check(report, f"sdk:{name}")["status"] == "info" for name in d.FRAMEWORKS)
    assert check(report, "sessions")["status"] == "info"
    assert "normal" in check(report, "sessions")["message"]
    assert check(report, "audit-root")["status"] == "info"
    assert "recording" not in {row["id"] for row in report["checks"]}
    assert not root.parent.exists()
    assert "ready to attach" not in json.dumps(report)


@pytest.mark.parametrize("framework", d.FRAMEWORKS)
def test_selected_missing_sdk_fails_and_imports_only_selection(tmp_path, sdk_imports, framework):
    report = d.collect_diagnostics(tmp_path, framework=framework)
    assert report["healthy"] is False
    row = check(report, f"sdk:{framework}")
    assert row["status"] == "error"
    assert "not installed" in row["message"]
    assert f'python -m pip install "contextos-auditor[{framework}]"' in row["action"]
    assert sdk_imports[1] == [d.FRAMEWORKS[framework][0]]


@pytest.mark.parametrize("framework", d.FRAMEWORKS)
def test_selected_importable_sdk_is_not_claimed_ready(tmp_path, sdk_imports, framework):
    healthy_sdk(sdk_imports, framework, "0.100.0")
    report = d.collect_diagnostics(tmp_path, framework)
    assert report["healthy"] is True
    row = check(report, f"sdk:{framework}")
    assert row["status"] == "ok"
    assert "broad advisory" in row["message"]
    assert "does not mean every version was tested" in row["message"]
    assert "0.100.0" in row["detail"]
    assert sdk_imports[1] == [d.FRAMEWORKS[framework][0]]
    assert "live provider capture" in check(report, "scope")["message"]


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ModuleNotFoundError("Missing nested dependency", name="pydantic"), "dependency"),
        (ModuleNotFoundError("Unknown missing name"), "dependency"),
        (ModuleNotFoundError("Missing SDK submodule", name="agents.tracing"), "dependency"),
        (ImportError("Cannot import name", name="agents"), "dependency"),
        (RuntimeError("Plugin initialization failed"), "initialization"),
        (ValueError("Plugin configuration malformed"), "initialization"),
        (PermissionError("Plugin cannot read its configuration"), "initialization"),
    ],
)
@pytest.mark.parametrize("selected", [True, False])
def test_broken_import_distinct_from_missing_and_unrelated_not_fatal(
    tmp_path, sdk_imports, error, category, selected,
):
    sdk_imports[0]["agents"] = error
    report = d.collect_diagnostics(tmp_path, "openai-agents" if selected else None)
    row = check(report, "sdk:openai-agents")
    assert row["status"] == "error"
    assert category in row["message"]
    assert "pip check" in row["action"]
    assert "not installed" not in row["message"]
    assert report["healthy"] is not selected
    assert d.support_report(report)["healthy"] is not selected


def test_optional_broken_sdk_keeps_local_detail_but_support_export_drops_it(tmp_path, sdk_imports):
    sdk_imports[0]["agents"] = RuntimeError("broken transitive dep")
    report = d.collect_diagnostics(tmp_path)
    row = check(report, "sdk:openai-agents")
    assert row["status"] == "error"
    assert row["detail"] == "broken transitive dep"
    assert report["healthy"]
    safe = d.support_report(report)
    assert safe["healthy"]
    assert check(safe, "sdk:openai-agents")["status"] == "error"
    assert "broken transitive dep" not in json.dumps(safe)
    assert "detail" not in check(safe, "sdk:openai-agents")


def test_sdk_exception_with_broken_str_cannot_crash_doctor(tmp_path, sdk_imports):
    class BrokenError(Exception):
        def __str__(self):
            raise RuntimeError("second plugin failure")

    sdk_imports[0]["agents"] = BrokenError()
    report = d.collect_diagnostics(tmp_path, "openai-agents")
    assert not report["healthy"]
    assert check(report, "sdk:openai-agents")["detail"] == "SDK exception detail is unavailable."


def test_sdk_version_attribute_failure_is_contained(tmp_path, sdk_imports):
    class BadSDK:
        @property
        def __version__(self):
            raise RuntimeError("bad lazy version")

    sdk_imports[0]["agents"] = BadSDK()
    report = d.collect_diagnostics(tmp_path, "openai-agents")
    assert not report["healthy"]
    assert "initialization" in check(report, "sdk:openai-agents")["message"]


@pytest.mark.parametrize("version", [None, "", "unknown", "vPRIVATE", "1.2-secret", 123, (), (1, "x"), True])
def test_unknown_sdk_versions_are_not_misclassified_as_supported(tmp_path, sdk_imports, version):
    healthy_sdk(sdk_imports, version=version)
    report = d.collect_diagnostics(tmp_path, "langgraph")
    row = check(report, "sdk:langgraph")
    assert row["status"] == "warning"
    assert "version is unknown" in row["message"]
    assert report["healthy"] is True


def test_version_objects_are_not_stringified(tmp_path, sdk_imports):
    class HostileVersion:
        def __str__(self) -> str:
            pytest.fail("Untrusted version must not execute __str__")
            return ""

    healthy_sdk(sdk_imports, version=HostileVersion())
    assert "unknown" in check(d.collect_diagnostics(tmp_path, "langgraph"), "sdk:langgraph")["message"]


@pytest.mark.parametrize(
    ("version", "status"),
    [("0.2.9", "warning"), ("0.3", "ok"), ((0, 3, 0), "ok"), ([1, 9], "ok"),
     ("1.5.0rc1", "ok"), ("1.5.0.post2", "ok"), ("2.0", "warning"), ("99.0.0", "warning")],
)
def test_version_ranges_are_advisory(tmp_path, sdk_imports, version, status):
    healthy_sdk(sdk_imports, version=version)
    report = d.collect_diagnostics(tmp_path, "langgraph")
    assert check(report, "sdk:langgraph")["status"] == status
    assert report["healthy"]
    if status == "warning":
        assert "outside" in check(report, "sdk:langgraph")["message"]


@pytest.mark.parametrize("version", [
    "1.15.14", "0.19.4", "1.0.0rc1", "0.4.0a2", "0.4.0b1",
    "0.4.0.post2", "0.4.0.dev1", "2.0.0rc1",
])
def test_exact_allowlisted_sdk_versions_survive_support_export(tmp_path, sdk_imports, version):
    healthy_sdk(sdk_imports, version=version)
    report = d.collect_diagnostics(tmp_path, "langgraph")
    safe = d.support_report(report)
    assert check(report, "sdk:langgraph")["version"] == version
    assert check(safe, "sdk:langgraph")["version"] == version
    assert set(check(safe, "sdk:langgraph")) == {"id", "status", "message", "action", "version"}
    assert "detail" not in check(safe, "sdk:langgraph")
    assert version not in check(safe, "sdk:langgraph")["message"]
    assert report["healthy"] == safe["healthy"]
    assert d.support_report(safe) == safe


@pytest.mark.parametrize(("version", "expected"), [
    ((0, 4, 0), "0.4.0"), ([1, 15, 14], "1.15.14"), ((0, 19), "0.19"),
])
def test_numeric_sequence_sdk_versions_export_only_as_strings(tmp_path, sdk_imports, version, expected):
    healthy_sdk(sdk_imports, version=version)
    report = d.collect_diagnostics(tmp_path, "langgraph")
    assert check(report, "sdk:langgraph")["version"] == expected
    assert check(d.support_report(report), "sdk:langgraph")["version"] == expected


@pytest.mark.parametrize("version", [
    "1.2.3+user/project", "1.2.3+private-host", "1.2.3+user", "1.2.3rc1/private",
    "1.2.3\n", " 1.2.3", "1.2.3 ", "1.2.3\t", "/Users/private/1.2.3",
    r"C:\Users\private\1.2.3", "1.2.3;echo private", "99999.1.2",
    {"version": "1.2.3"}, ["1", "2", "3"], [1, 2, True], (99999, 1), True, None,
])
def test_adversarial_sdk_versions_are_omitted_from_collector_and_safe_export(
    tmp_path, sdk_imports, version,
):
    healthy_sdk(sdk_imports, version=version)
    report = d.collect_diagnostics(tmp_path, "langgraph")
    assert "version" not in check(report, "sdk:langgraph")
    assert "version" not in check(d.support_report(report), "sdk:langgraph")
    # The export boundary also validates caller-modified reports independently
    # of the collector; it must not trust an already-populated version field.
    check(report, "sdk:langgraph")["version"] = version
    safe = d.support_report(report)
    assert "version" not in check(safe, "sdk:langgraph")
    assert "private" not in json.dumps(safe)


def test_version_field_is_only_allowed_on_recognized_sdk_checks(tmp_path, sdk_imports):
    healthy_sdk(sdk_imports)
    report = d.collect_diagnostics(tmp_path, "langgraph")
    for row in report["checks"]:
        row["version"] = "1.2.3"
    safe = d.support_report(report)
    assert all("version" not in row for row in safe["checks"] if not row["id"].startswith("sdk:"))
    check(report, "sdk:langgraph")["message"] = "private injected message"
    assert "version" not in check(d.support_report(report), "sdk:langgraph")


def test_unknown_framework_rejected_before_any_import_or_io(tmp_path, sdk_imports):
    with pytest.raises(ValueError, match="Unknown framework"):
        d.collect_diagnostics(tmp_path / "no-create", "not-a-framework")
    assert not sdk_imports[1]
    assert not (tmp_path / "no-create").exists()


@pytest.mark.parametrize(("selected", "expected"), [(None, True), ("crewai", True), ("langgraph", False)])
@pytest.mark.parametrize("minor", [14, 15])
def test_crewai_python_warning_even_if_base_and_sdk_import(tmp_path, sdk_imports, monkeypatch, selected, expected, minor):
    healthy_sdk(sdk_imports, "crewai", "1.0")
    healthy_sdk(sdk_imports)
    monkeypatch.setattr(d.sys, "version_info", (3, minor, 0))
    report = d.collect_diagnostics(tmp_path, selected)
    assert report["healthy"]
    assert ("python:crewai" in {row["id"] for row in report["checks"]}) is expected
    if expected:
        assert check(report, "python:crewai")["status"] == "warning"
        assert "Python 3.10-3.13" in check(report, "python:crewai")["action"]


def test_crewai_python_313_has_no_advisory(tmp_path, sdk_imports, monkeypatch):
    healthy_sdk(sdk_imports, "crewai", "1.0")
    monkeypatch.setattr(d.sys, "version_info", (3, 13, 0))
    assert "python:crewai" not in {row["id"] for row in d.collect_diagnostics(tmp_path, "crewai")["checks"]}


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "  True  "])
def test_tracing_disable_is_actionable_without_copying_value(tmp_path, sdk_imports, monkeypatch, value):
    healthy_sdk(sdk_imports, "openai-agents")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", value)
    report = d.collect_diagnostics(tmp_path, "openai-agents")
    row = check(report, "tracing:openai-agents")
    assert row["status"] == "error"
    assert "cannot capture" in row["message"]
    assert "Unset OPENAI_AGENTS_DISABLE_TRACING" in row["action"]
    assert set(row) == {"id", "status", "message", "action"}
    assert not report["healthy"]
    assert "tracing:openai-agents" not in {row["id"] for row in d.collect_diagnostics(tmp_path)["checks"]}


@pytest.mark.parametrize("value", ["0", "false", "", "no", "secret-arbitrary-value"])
def test_tracing_unset_or_false_does_not_promise_capture(tmp_path, sdk_imports, monkeypatch, value):
    healthy_sdk(sdk_imports, "openai-agents")
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", value)
    report = d.collect_diagnostics(tmp_path, "openai-agents")
    assert report["healthy"]
    assert "not checked" in check(report, "tracing:openai-agents")["message"]
    assert "secret-arbitrary-value" not in json.dumps(report)


def test_import_noise_never_contaminates_json_and_streams_are_restored(tmp_path, monkeypatch, capsys):
    module = "_diagnostics_noisy_sdk"
    (tmp_path / f"{module}.py").write_text(
        "import os, sys\n"
        "print('secret-import-stdout')\n"
        "print('secret-import-stderr', file=sys.stderr)\n"
        "os.write(1, b'secret-native-stdout\\xff\\n')\n"
        "os.write(2, b'secret-native-stderr\\xff\\n')\n"
        "raise RuntimeError('private exception')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(d, "_probe_sdk", lambda name: _REAL_PROBE_SDK(module))
    original_stdout, original_stderr = sys.stdout, sys.stderr
    report = d.collect_diagnostics(tmp_path, "crewai")
    assert (sys.stdout, sys.stderr) == (original_stdout, original_stderr)
    print(json.dumps(d.support_report(report)))
    captured = capsys.readouterr()
    assert not captured.err
    assert json.loads(captured.out)["schema_version"] == 1
    assert "secret-import" not in captured.out
    assert "secret-native" not in captured.out
    assert "private exception" not in captured.out
    assert check(report, "sdk:crewai")["detail"] == "private exception"


@pytest.fixture
def probe_module(tmp_path, monkeypatch):
    module = "_diagnostics_isolated_test_sdk"
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    def write_module(source):
        (tmp_path / f"{module}.py").write_text(source, encoding="utf-8")
        return module

    return write_module


@pytest.mark.parametrize(("source", "code"), [
    ("raise ModuleNotFoundError('missing nested', name='private_dependency')", "dependency"),
    ("raise ImportError('broken transitive dep')", "dependency"),
    ("raise RuntimeError('broken transitive dep')", "broken"),
    ("raise ValueError('private invalid config')", "broken"),
    ("class Error(Exception):\n"
     "    def __str__(self):\n"
     "        raise RuntimeError('private detail error')\n"
     "raise Error()", "broken"),
    ("__version__ = '0.4.0'", "imported"),
    ("__version__ = (0, 4, 0)", "imported"),
    ("class Version:\n"
     "    def __str__(self):\n"
     "        raise RuntimeError('must not stringify')\n"
     "__version__ = Version()", "imported"),
])
def test_real_subprocess_uses_worker_exception_and_version_boundary(probe_module, source, code):
    result = _REAL_PROBE_SDK(probe_module(source))
    assert result["code"] == code


def test_real_probe_missing_top_level_is_distinct_from_broken_dependency():
    result = _REAL_PROBE_SDK("_contextos_diagnostic_missing_top_level_7dc389")
    assert result["code"] == "missing"


def test_native_output_after_success_does_not_corrupt_protocol(probe_module, capsys):
    result = _REAL_PROBE_SDK(probe_module(
        "import atexit, os\n"
        "atexit.register(lambda: os.write(1, b'private native shutdown output\\xff'))\n"
        "os.write(2, b'private native stderr\\xff')\n"
        "__version__ = '0.4.0'\n"
    ))
    assert result == {"code": "imported", "version": "0.4.0"}
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("selected", [True, False])
def test_hanging_sdk_is_bounded_error_and_optional_probe_remains_nonblocking(
    tmp_path, monkeypatch, probe_module, selected,
):
    module = probe_module("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(d, "_SDK_TIMEOUT", 0.15)
    monkeypatch.setattr(
        d, "_probe_sdk",
        lambda name: _REAL_PROBE_SDK(module) if name == "agents" else {"code": "missing"},
    )
    start = time.monotonic()
    report = d.collect_diagnostics(tmp_path, "openai-agents" if selected else None)
    assert time.monotonic() - start < 3
    row = check(report, "sdk:openai-agents")
    assert row["status"] == "error"
    assert "timed out" in row["message"]
    assert "pip check" in row["action"]
    assert report["healthy"] is not selected
    assert d.support_report(report)["healthy"] is not selected


@pytest.mark.parametrize("code", ["timeout", "output-limit", "probe-error"])
@pytest.mark.parametrize("selected", [True, False])
def test_isolated_probe_failures_obey_selected_health_semantics(tmp_path, monkeypatch, code, selected):
    monkeypatch.setattr(d, "_probe_sdk", lambda name: {"code": code})
    report = d.collect_diagnostics(tmp_path, "langgraph" if selected else None)
    assert check(report, "sdk:langgraph")["status"] == "error"
    assert report["healthy"] is not selected
    assert d.support_report(report)["healthy"] is not selected


@pytest.mark.parametrize("fd", [1, 2])
def test_native_output_is_memory_bounded_and_stopped(probe_module, monkeypatch, fd):
    monkeypatch.setattr(d, "_SDK_OUTPUT_LIMIT", 16 * 1024)
    module = probe_module(f"import os\nwhile True:\n    os.write({fd}, b'x' * 8192)\n")
    start = time.monotonic()
    result = _REAL_PROBE_SDK(module)
    assert result == {"code": "output-limit"}
    assert time.monotonic() - start < 3


@pytest.fixture
def windows_probe_lifecycle(monkeypatch):
    events = []
    state = {"mode": "normal", "failure": None}

    class Job:
        CREATE_SUSPENDED = 0x00000004

        def __init__(self):
            events.append("job-create")
            if state["failure"] == "job-create":
                raise OSError("private job creation failure")

        def attach_and_resume(self, process):
            events.append("job-attach")
            if state["failure"] == "job-attach":
                raise OSError("private job assignment failure")
            events.append("job-resume")
            process.resumed = True

        def close(self):
            events.append("job-close")
            if state["failure"] == "job-close":
                raise OSError("private job cleanup failure")

    class Process:
        def __init__(self, command, **kwargs):
            events.append("process-create")
            assert kwargs["creationflags"] == Job.CREATE_SUSPENDED
            assert kwargs["start_new_session"] is False
            if state["failure"] == "process-create":
                raise OSError("private process creation failure")
            payload = command[-1] + json.dumps({"code": "imported", "version": "0.4.0"})
            if state["mode"] == "output-limit":
                payload = "x" * (d._SDK_OUTPUT_LIMIT + 1)
            self.stdout = io.BytesIO(payload.encode())
            self.stderr = io.BytesIO()
            self.returncode = None
            self.resumed = False
            state["process"] = self

        def poll(self):
            return self.returncode

        def kill(self):
            events.append("process-kill")
            self.returncode = -9

        def wait(self, timeout):
            events.append("process-wait")
            if self.returncode is not None:
                return self.returncode
            assert self.resumed
            if state["mode"] == "timeout":
                raise subprocess.TimeoutExpired("probe", timeout)
            self.returncode = 0
            return 0

    monkeypatch.setattr(d.sys, "platform", "win32")
    monkeypatch.setattr(d, "_WindowsJob", Job)
    monkeypatch.setattr(d.subprocess, "Popen", Process)
    return events, state


@pytest.mark.parametrize(("mode", "code"), [
    ("normal", "imported"), ("timeout", "timeout"), ("output-limit", "output-limit"),
])
def test_windows_probe_owns_process_before_resume_and_closes_job_on_every_exit(
    windows_probe_lifecycle, mode, code,
):
    events, state = windows_probe_lifecycle
    state["mode"] = mode
    assert _REAL_PROBE_SDK("unused")["code"] == code
    assert events[:4] == ["job-create", "process-create", "job-attach", "job-resume"]
    assert events.count("job-close") == 1
    assert events.index("job-close") > events.index("process-wait")
    assert state["process"].stdout.closed
    assert state["process"].stderr.closed


@pytest.mark.parametrize("failure", ["job-create", "process-create", "job-attach", "job-close"])
def test_windows_ownership_failure_is_closed_and_never_runs_unowned_sdk(
    windows_probe_lifecycle, failure,
):
    events, state = windows_probe_lifecycle
    state["failure"] = failure
    assert _REAL_PROBE_SDK("unused")["code"] == "probe-error"
    if failure != "job-close":
        assert "job-resume" not in events
    if failure != "job-create":
        assert events.count("job-close") == 1
    if failure == "job-attach":
        assert events.index("process-kill") > events.index("job-close")
        assert state["process"].stdout.closed
        assert state["process"].stderr.closed


@pytest.fixture
def windows_kernel(monkeypatch):
    import ctypes

    kernel = SimpleNamespace()
    for name in (
        "CreateJobObjectW", "SetInformationJobObject", "AssignProcessToJobObject",
        "TerminateJobObject", "QueryInformationJobObject", "CloseHandle",
        "CreateToolhelp32Snapshot", "Thread32First", "Thread32Next", "OpenThread",
        "GetProcessIdOfThread", "ResumeThread",
    ):
        setattr(kernel, name, Mock(return_value=1))
    kernel.CreateJobObjectW.return_value = 100
    kernel.CreateToolhelp32Snapshot.return_value = 200
    kernel.OpenThread.return_value = 300
    kernel.GetProcessIdOfThread.return_value = 321

    def first_thread(snapshot, entry):
        assert snapshot == 200
        assert entry._obj.dwSize == 28
        entry._obj.th32OwnerProcessID = 321
        entry._obj.th32ThreadID = 123
        return 1

    kernel.Thread32First.side_effect = first_thread
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError("private Windows API failure"), raising=False)
    return kernel


def test_windows_job_is_private_kill_on_close_and_attaches_before_resuming(windows_kernel):
    kernel = windows_kernel
    job = d._WindowsJob()
    process = SimpleNamespace(pid=321, _handle=400)
    kernel.CreateJobObjectW.assert_called_once_with(None, None)
    limits = kernel.SetInformationJobObject.call_args.args[2]._obj
    assert limits.BasicLimitInformation.LimitFlags == 0x00002000
    job.attach_and_resume(process)
    kernel.AssignProcessToJobObject.assert_called_once_with(100, 400)
    kernel.GetProcessIdOfThread.assert_called_once_with(300)
    kernel.ResumeThread.assert_called_once_with(300)
    job.close()
    kernel.TerminateJobObject.assert_called_once_with(100, 1)
    assert [call.args[0] for call in kernel.CloseHandle.call_args_list] == [300, 200, 100]
    job.close()
    kernel.TerminateJobObject.assert_called_once()


def test_windows_job_never_resumes_a_thread_whose_handle_has_different_owner(windows_kernel):
    kernel = windows_kernel
    kernel.GetProcessIdOfThread.return_value = 999
    job = d._WindowsJob()
    try:
        with pytest.raises(OSError, match="ownership"):
            job.attach_and_resume(SimpleNamespace(pid=321, _handle=400))
        kernel.ResumeThread.assert_not_called()
    finally:
        job.close()


def test_windows_job_assignment_failure_never_resumes_probe(windows_kernel):
    kernel = windows_kernel
    kernel.AssignProcessToJobObject.return_value = 0
    job = d._WindowsJob()
    try:
        with pytest.raises(OSError):
            job.attach_and_resume(SimpleNamespace(pid=321, _handle=400))
        kernel.CreateToolhelp32Snapshot.assert_not_called()
        kernel.ResumeThread.assert_not_called()
    finally:
        job.close()


def test_windows_job_setup_failure_closes_its_handle(windows_kernel):
    windows_kernel.SetInformationJobObject.return_value = 0
    with pytest.raises(OSError):
        d._WindowsJob()
    windows_kernel.CloseHandle.assert_called_once_with(100)
    windows_kernel.AssignProcessToJobObject.assert_not_called()


def test_windows_job_termination_failure_still_closes_kill_on_close_handle(windows_kernel):
    windows_kernel.TerminateJobObject.return_value = 0
    job = d._WindowsJob()
    with pytest.raises(OSError):
        job.close()
    windows_kernel.CloseHandle.assert_called_once_with(100)


def test_windows_job_does_not_finish_cleanup_until_descendants_exit(windows_kernel, monkeypatch):
    active = iter([1, 0])
    clock = SimpleNamespace(monotonic=lambda: 0.0, sleep=Mock())
    monkeypatch.setattr(d, "time", clock)

    def accounting_result(handle, category, accounting, size, returned):
        accounting._obj.ActiveProcesses = next(active)
        return 1

    windows_kernel.QueryInformationJobObject.side_effect = accounting_result
    job = d._WindowsJob()
    job.close()
    assert windows_kernel.QueryInformationJobObject.call_count == 2
    clock.sleep.assert_called_once_with(0.01)
    windows_kernel.CloseHandle.assert_called_once_with(100)


def test_windows_job_waits_for_descendants_with_bounded_cleanup(windows_kernel, monkeypatch):
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(d, "time", SimpleNamespace(monotonic=lambda: next(ticks)))

    def still_running(handle, category, accounting, size, returned):
        accounting._obj.ActiveProcesses = 1
        return 1

    windows_kernel.QueryInformationJobObject.side_effect = still_running
    job = d._WindowsJob()
    with pytest.raises(subprocess.TimeoutExpired):
        job.close()
    windows_kernel.CloseHandle.assert_called_once_with(100)


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows Job Object ownership regression")
@pytest.mark.parametrize(("mode", "code"), [
    ("normal", "imported"), ("timeout", "timeout"), ("output-limit", "output-limit"),
])
def test_windows_job_reaps_import_spawned_child_and_preserves_unrelated_process(
    tmp_path, monkeypatch, probe_module, mode, code,
):
    import ctypes as c

    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [c.c_uint32, c.c_int, c.c_uint32]
    kernel.OpenProcess.restype = c.c_void_p
    kernel.WaitForSingleObject.argtypes = [c.c_void_p, c.c_uint32]
    kernel.WaitForSingleObject.restype = c.c_uint32
    kernel.TerminateProcess.argtypes = [c.c_void_p, c.c_uint32]
    kernel.TerminateProcess.restype = c.c_int
    kernel.CloseHandle.argtypes = [c.c_void_p]
    kernel.CloseHandle.restype = c.c_int
    pid_file = tmp_path / "owned-child.pid"
    release = tmp_path / "release-probe"
    ending = {
        "normal": "__version__ = '0.4.0'\n",
        "timeout": "time.sleep(60)\n",
        "output-limit": "while True:\n    os.write(1, b'x' * 8192)\n",
    }[mode]
    module = probe_module(
        "import os, subprocess, sys, time\nfrom pathlib import Path\n"
        "child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(60)'], "
        "stdin=subprocess.DEVNULL)\n"
        f"pid_path = Path({str(pid_file)!r})\n"
        "pid_path.with_suffix('.pending').write_text(str(child.pid), encoding='ascii')\n"
        "pid_path.with_suffix('.pending').replace(pid_path)\n"
        f"while not Path({str(release)!r}).exists():\n    time.sleep(0.01)\n"
        + ending
    )
    monkeypatch.setattr(d, "_SDK_TIMEOUT", 5)
    monkeypatch.setattr(d, "_SDK_OUTPUT_LIMIT", 16 * 1024)
    result = []
    worker = threading.Thread(target=lambda: result.append(_REAL_PROBE_SDK(module)), daemon=True)
    # This process belongs to the test, never the SDK probe's private job.
    unrelated = subprocess.Popen(
        [sys.executable, "-B", "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    child_handle = None
    try:
        worker.start()
        deadline = time.monotonic() + 4
        while not pid_file.exists() and worker.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists(), result
        # The probe waits for our release, so this opens a still-live owned
        # child, not a possibly recycled PID after cleanup has already run.
        child_handle = kernel.OpenProcess(0x00100000 | 0x0001, False, int(pid_file.read_text()))
        assert child_handle, c.get_last_error()
        release.touch()
        worker.join(timeout=8)
        assert not worker.is_alive()
        assert result[0]["code"] == code, result
        assert kernel.WaitForSingleObject(child_handle, 1000) == 0
        assert unrelated.poll() is None
    finally:
        release.touch()
        worker.join(timeout=8)
        if child_handle:
            if kernel.WaitForSingleObject(child_handle, 0) != 0:
                kernel.TerminateProcess(child_handle, 1)
                kernel.WaitForSingleObject(child_handle, 1000)
            kernel.CloseHandle(child_handle)
        unrelated.kill()
        unrelated.wait(timeout=2)


@pytest.mark.parametrize("source", ["raise SystemExit(0)", "import os\nos._exit(3)"])
def test_abrupt_sdk_process_exit_is_not_import_success(probe_module, source):
    result = _REAL_PROBE_SDK(probe_module(source))
    assert result["code"] == "probe-error"


def test_probe_inherits_same_interpreter_and_explicit_environment_without_recording_values(
    probe_module, monkeypatch,
):
    monkeypatch.setenv("DIAGNOSTIC_TEST_INHERITED_SETTING", "private-sdk-setting")
    module = probe_module(
        "import os, sys\n"
        f"assert sys.executable == {sys.executable!r}\n"
        "assert os.environ['DIAGNOSTIC_TEST_INHERITED_SETTING'] == 'private-sdk-setting'\n"
        "__version__ = '0.4.0'\n"
    )
    result = _REAL_PROBE_SDK(module)
    assert result == {"code": "imported", "version": "0.4.0"}
    assert "private-sdk-setting" not in json.dumps(result)


def test_sdk_side_effects_do_not_mutate_parent_environment_or_module_cache(probe_module, monkeypatch):
    monkeypatch.delenv("DIAGNOSTIC_TEST_SDK_SIDE_EFFECT", raising=False)
    module = probe_module(
        "import os\n"
        "os.environ['DIAGNOSTIC_TEST_SDK_SIDE_EFFECT'] = 'private'\n"
        "__version__ = '0.4.0'\n"
    )
    assert module not in sys.modules
    assert _REAL_PROBE_SDK(module)["code"] == "imported"
    assert module not in sys.modules
    assert "DIAGNOSTIC_TEST_SDK_SIDE_EFFECT" not in os.environ


def test_probe_does_not_inject_current_directory_or_auditor_source_paths(tmp_path, monkeypatch):
    module = "_contextos_diagnostic_local_shadow_741fd3"
    (tmp_path / f"{module}.py").write_text(
        "raise RuntimeError('must not import implicit cwd shadow')", encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", "")
    assert _REAL_PROBE_SDK(module)["code"] == "missing"


def test_probe_start_failure_is_explicit_and_does_not_echo_into_support(tmp_path, monkeypatch):
    def fail_process(*args, **kwargs):
        raise PermissionError("private process-start failure")

    monkeypatch.setattr(d.subprocess, "Popen", fail_process)
    monkeypatch.setattr(d, "_probe_sdk", _REAL_PROBE_SDK)
    report = d.collect_diagnostics(tmp_path, "langgraph")
    row = check(report, "sdk:langgraph")
    assert row["status"] == "error"
    assert row["detail"] == "private process-start failure"
    assert "private" not in json.dumps(d.support_report(report))
    assert not report["healthy"]


@pytest.mark.parametrize("payload", ["private invalid json", "[]", '{"code": []}', '{"code": "private"}'])
def test_unrecognized_probe_protocol_is_fixed_error(monkeypatch, payload):
    monkeypatch.setattr(
        d, "_SDK_PROBE_CODE",
        f"import sys; sys.stdout.write(sys.argv[2] + {payload!r})",
    )
    assert _REAL_PROBE_SDK("unused") == {"code": "probe-error"}


def test_support_report_excludes_paths_names_content_env_and_exception_text(tmp_path, sdk_imports, monkeypatch):
    root = tmp_path / "private-user" / "private-audit"
    session = root / "secret-session-id"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text("secret prompt and tool output", encoding="utf-8")
    (session / "session.json").write_text("private hostname", encoding="utf-8")
    sdk_imports[0]["agents"] = ImportError(f"credential=secret-api-key at {session}")
    monkeypatch.setenv("SECRET_PROVIDER_KEY", "secret-api-key")
    report = d.collect_diagnostics(root, "openai-agents")
    safe = d.support_report(report)
    serialized = json.dumps(safe)
    assert "secret-api-key" in check(report, "sdk:openai-agents")["detail"]
    for secret in ("private-user", "private-audit", "secret-session-id", "secret-api-key",
                   "secret prompt", "tool output", "private hostname", str(tmp_path), sys.executable):
        assert secret not in serialized
    assert "dependency" in check(safe, "sdk:openai-agents")["message"]
    assert "pip check" in check(safe, "sdk:openai-agents")["action"]
    assert check(safe, "sessions")["message"].startswith("Session event files are present")
    assert not safe["healthy"]
    assert "detail" not in serialized
    assert set(safe) == {"schema_version", "environment", "checks", "healthy"}


@pytest.mark.parametrize("version", ["0.4.0+/Users/private/token", "secret\nversion", "0.4.0secret",
                                   "0.4.0+privatehostname", "12345678901234567890.1"])
def test_malicious_versions_do_not_reach_shared_text(tmp_path, sdk_imports, version):
    healthy_sdk(sdk_imports, version=version)
    report = d.collect_diagnostics(tmp_path, "langgraph")
    assert version not in json.dumps(d.support_report(report))
    assert check(report, "sdk:langgraph")["status"] == "warning"


def test_support_schema_allowlists_every_level_and_reconstructs_actions(tmp_path):
    original = d.collect_diagnostics(tmp_path)
    altered = copy.deepcopy(original)
    altered["schema_version"] = "secret"
    altered["secret"] = {"nested": ["private"]}
    altered["healthy"] = "secret"
    altered["environment"].update({
        "auditor_version": "1.2.3-private",
        "python_version": "/private/3.14",
        "python_implementation": "user-host",
        "platform": "Linux private-host",
        "in_virtualenv": "private-user",
        "prompt": "private-prompt",
        "environment": {"SECRET": "private-key"},
    })
    for row in altered["checks"]:
        row.update(action="private-action", detail="private-detail", extra={"key": "private-key"})
    altered["checks"].extend([
        {"id": "secret-check", "status": "error", "message": "private-message"},
        {"id": ["private-check"]},
        "private-check",
    ])
    safe = d.support_report(altered)
    assert "private" not in json.dumps(safe)
    assert "secret" not in json.dumps(safe)
    assert safe["schema_version"] == 1
    assert safe["healthy"] is False
    assert safe["environment"] == {
        "auditor_version": "unknown", "python_version": "unknown",
        "python_implementation": "unknown", "platform": "unknown", "in_virtualenv": None,
    }
    assert len(safe["checks"]) == len(original["checks"])
    assert all(set(row) == {"id", "status", "message", "action"} for row in safe["checks"])
    assert safe["checks"][0]["action"] == original["checks"][0]["action"]


@pytest.mark.parametrize("field", ["message", "status"])
def test_unrecognized_known_check_text_becomes_fixed_error(tmp_path, field):
    report = d.collect_diagnostics(tmp_path)
    report["checks"][0][field] = "secret-check-value"
    safe = d.support_report(report)
    assert "secret-check-value" not in json.dumps(safe)
    assert safe["checks"][0]["status"] == "error"
    assert "could not be validated" in safe["checks"][0]["message"]
    assert not safe["healthy"]


def test_support_report_is_nonmutating_and_idempotent(tmp_path):
    report = d.collect_diagnostics(tmp_path)
    previous = copy.deepcopy(report)
    safe = d.support_report(report)
    assert report == previous
    assert d.support_report(safe) == safe
    assert safe["healthy"]
    assert set(safe["environment"]) == {
        "auditor_version", "python_version", "python_implementation", "platform", "in_virtualenv",
    }


def test_support_report_handles_malformed_containers():
    safe = d.support_report({"environment": ["secret"], "checks": "secret", "healthy": {"secret": True}})
    assert not safe["healthy"]
    assert "secret" not in json.dumps(safe)


def test_session_discovery_never_opens_user_contents(tmp_path, monkeypatch):
    (tmp_path / "session-with-events").mkdir()
    (tmp_path / "session-with-events" / "events.jsonl").write_bytes(b"\xff private corrupt events")
    (tmp_path / "metadata-only").mkdir()
    (tmp_path / "metadata-only" / "session.json").write_text("private", encoding="utf-8")
    (tmp_path / "not-an-event-file").mkdir()
    (tmp_path / "not-an-event-file" / "events.jsonl").mkdir()
    (tmp_path / "unrelated-file").write_text("private", encoding="utf-8")

    def forbidden(*args, **kwargs):
        pytest.fail("Diagnostics must not open real session files")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    assert d._session_count(tmp_path) == 1
    report = d.collect_diagnostics(tmp_path)
    assert report["healthy"]
    assert "present" in check(report, "sessions")["message"]


def test_only_metadata_is_not_evidence_of_recorded_usage(tmp_path):
    session = tmp_path / "metadata-only"
    session.mkdir()
    (session / "session.json").write_text("private", encoding="utf-8")
    assert "No session event files" in check(d.collect_diagnostics(tmp_path), "sessions")["message"]


def test_discovery_skips_session_and_event_symlinks(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "events.jsonl").write_text("private", encoding="utf-8")
    (tmp_path / "linked-session").symlink_to(target, target_is_directory=True)
    indirect = tmp_path / "indirect"
    indirect.mkdir()
    (indirect / "events.jsonl").symlink_to(target / "events.jsonl")
    assert d._session_count(tmp_path) == 1


@pytest.mark.parametrize("suffix", ["", "child", "child/grandchild"])
def test_root_or_parent_file_is_explicit_error(tmp_path, suffix):
    file = tmp_path / "a-file"
    file.write_bytes(b"existing data")
    report = d.collect_diagnostics(file / suffix, check_recording=True)
    assert not report["healthy"]
    assert check(report, "audit-root")["status"] == "error"
    assert "not a directory" in check(report, "audit-root")["message"]
    assert check(report, "recording")["status"] == "error"
    assert file.read_bytes() == b"existing data"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a-file"]


def test_dangling_root_symlink_is_not_treated_as_creatable(tmp_path):
    root = tmp_path / "dangling"
    root.symlink_to(tmp_path / "nonexistent", target_is_directory=True)
    report = d.collect_diagnostics(root)
    assert not report["healthy"]
    assert "not a directory" in check(report, "audit-root")["message"]


@pytest.mark.parametrize("existing", [True, False])
def test_root_write_permissions_checked_at_nearest_existing_ancestor(tmp_path, monkeypatch, existing):
    root = tmp_path if existing else tmp_path / "not-yet" / "audit"
    calls = []

    def access(path, flags):
        calls.append((path, flags))
        return False

    monkeypatch.setattr(d.os, "access", access)
    report = d.collect_diagnostics(root, check_recording=True)
    assert calls == [(tmp_path, os.R_OK | os.W_OK | os.X_OK)]
    assert check(report, "audit-root")["status"] == "error"
    assert check(report, "recording")["status"] == "error"
    assert not report["healthy"]


@pytest.mark.parametrize("error", [PermissionError("private denial"), OSError("private I/O failure")])
def test_session_discovery_io_failure_is_not_empty_success(tmp_path, monkeypatch, error):
    def fail_scan(path):
        raise error

    monkeypatch.setattr(d.os, "scandir", fail_scan)
    report = d.collect_diagnostics(tmp_path)
    assert check(report, "sessions")["status"] == "error"
    assert not report["healthy"]
    assert "private" not in json.dumps(d.support_report(report))


def test_root_stat_io_failure_is_explicit(tmp_path, monkeypatch):
    def fail_stat(self, **kwargs):
        raise OSError("private root failure")

    monkeypatch.setattr(Path, "stat", fail_stat)
    report = d.collect_diagnostics(tmp_path)
    assert check(report, "audit-root")["status"] == "error"
    assert not report["healthy"]


def test_session_event_stat_permission_failure_is_explicit(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    real_stat = Path.stat

    def fail_event_stat(self, **kwargs):
        if self.name == "events.jsonl":
            raise PermissionError("private event metadata")
        return real_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_event_stat)
    report = d.collect_diagnostics(tmp_path)
    assert check(report, "sessions")["status"] == "error"
    assert not report["healthy"]


def test_synthetic_selftest_is_real_isolated_cleans_and_preserves_existing_data(tmp_path, monkeypatch):
    session = tmp_path / "existing-session"
    session.mkdir()
    (session / "events.jsonl").write_bytes(b"private real events\n")
    (session / "session.json").write_bytes(b"private real metadata\n")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in session.iterdir()}
    from contextos_auditor._internal import tokens

    initial_encoder = tokens._ENC
    report = d.collect_diagnostics(tmp_path, check_recording=True)
    assert check(report, "recording")["status"] == "ok", check(report, "recording")
    assert "Synthetic recorder self-test" in check(report, "recording")["message"]
    assert "not proof" in check(report, "recording")["message"]
    assert report["healthy"]
    assert tokens._ENC is initial_encoder
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing-session"]


def test_selftest_uses_nearest_existing_ancestor_without_creating_audit_root(tmp_path):
    report = d.collect_diagnostics(tmp_path / "missing" / "audit", check_recording=True)
    assert report["healthy"], check(report, "recording")
    assert check(report, "recording")["status"] == "ok"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("failure", "status", "message"), [
    (None, "ok", "self-test passed"),
    ("PermissionError", "error", "filesystem permissions"),
    ("OSError", "error", "filesystem error"),
])
def test_recorder_child_resolves_windows_home_inside_synthetic_workspace(
    tmp_path, monkeypatch, failure, status, message,
):
    """Exercise Windows expanduser rules even when the host has a pwd database."""
    monkeypatch.setenv("HOME", "private-real-home")
    monkeypatch.setenv("USERPROFILE", "private-real-profile")
    real_run = subprocess.run
    seen = []
    windows_home = """
import ntpath
from pathlib import Path
def windows_home(cls):
    expanded = ntpath.expanduser("~")
    if expanded == "~":
        raise RuntimeError("Could not determine home directory.")
    return cls(expanded)
Path.home = classmethod(windows_home)
"""
    if failure:
        windows_home += (
            "Path.write_text = lambda *args, **kwargs: "
            f"(_ for _ in ()).throw({failure}('private write failure'))\n"
        )

    def windows_semantics_run(command, **kwargs):
        seen.append((command[-1], kwargs["env"]))
        command = list(command)
        command[3] = windows_home + "\n" + command[3].replace(
            "sys.exit(_recording_main(sys.argv[1]))",
            "result = _recording_main(sys.argv[1]); "
            "from contextos_auditor._internal.tokens import CACHE_DIR; "
            "assert CACHE_DIR.is_relative_to(Path(sys.argv[1])); sys.exit(result)",
        )
        return real_run(command, **kwargs)

    monkeypatch.setattr(d.subprocess, "run", windows_semantics_run)
    report = d.collect_diagnostics(tmp_path, check_recording=True)
    row = check(report, "recording")
    assert row["status"] == status, row.get("detail", row["message"])
    assert message in row["message"], row.get("detail", row["message"])
    directory, environment = seen[0]
    assert environment["HOME"] == environment["USERPROFILE"] == directory
    assert "private-real" not in json.dumps(environment)
    assert "private" not in json.dumps(d.support_report(report))
    assert os.environ["HOME"] == "private-real-home"
    assert os.environ["USERPROFILE"] == "private-real-profile"
    assert list(tmp_path.iterdir()) == []


def test_selftest_inherits_no_secrets_and_never_initializes_tokenizer_exporter_or_network(tmp_path, monkeypatch):
    for key in ("CONTEXTOS_AUDITOR_OTEL_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT",
                "OPENAI_API_KEY", "TIKTOKEN_CACHE_DIR", "CONTEXTOS_AUDITOR_OTEL"):
        monkeypatch.setenv(key, "private-value")
    monkeypatch.setenv("CONTEXTOS_TIKTOKEN_DOWNLOAD", "1")
    monkeypatch.setenv("CONTEXTOS_OTEL_ENDPOINT", "http://127.0.0.1:4318/v1/traces")
    real_run = subprocess.run
    seen = []
    guard = """
import builtins, socket, urllib.request
attempts = []
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.startswith(("tiktoken", "opentelemetry", "agents", "crewai", "langchain", "autogen")):
        attempts.append(name)
        raise AssertionError("Unexpected optional import")
    return original_import(name, *args, **kwargs)
def forbidden(*args, **kwargs):
    attempts.append("network")
    raise AssertionError("Network access attempted")
builtins.__import__ = guarded_import
socket.socket.connect = forbidden
socket.create_connection = forbidden
urllib.request.urlopen = forbidden
"""

    def guarded_run(command, **kwargs):
        assert set(kwargs["env"]) <= {"PYTHONPATH", "SystemRoot", "HOME", "USERPROFILE"}
        assert "private-value" not in json.dumps(kwargs["env"])
        assert command[1] == "-BS"
        directory = Path(command[-1])
        assert directory.parent == tmp_path
        assert kwargs["env"]["HOME"] == kwargs["env"]["USERPROFILE"] == str(directory)
        seen.append(directory)
        guarded_command = list(command)
        guarded_command[3] = (
            guard + "\n"
            + command[3].replace(
                "sys.exit(_recording_main(sys.argv[1]))",
                "assert _recording_main(sys.argv[1]) == 0",
            )
            + "\nassert not attempts, attempts"
        )
        return real_run(guarded_command, **kwargs)

    monkeypatch.setattr(d.subprocess, "run", guarded_run)
    report = d.collect_diagnostics(tmp_path, check_recording=True)
    assert check(report, "recording")["status"] == "ok", check(report, "recording")
    assert seen and not seen[0].exists()
    assert os.environ["OPENAI_API_KEY"] == "private-value"
    assert os.environ["CONTEXTOS_TIKTOKEN_DOWNLOAD"] == "1"
    assert os.environ["CONTEXTOS_OTEL_ENDPOINT"] == "http://127.0.0.1:4318/v1/traces"


@pytest.mark.parametrize("error", [
    PermissionError("private denied"),
    OSError("private disk failure"),
    subprocess.TimeoutExpired("private-command", 30),
])
def test_selftest_process_failures_are_errors_and_cleanup(tmp_path, monkeypatch, error):
    def fail_run(*args, **kwargs):
        raise error

    monkeypatch.setattr(d.subprocess, "run", fail_run)
    report = d.collect_diagnostics(tmp_path, check_recording=True)
    assert check(report, "recording")["status"] == "error"
    assert not report["healthy"]
    assert list(tmp_path.iterdir()) == []
    assert "private" not in json.dumps(d.support_report(report))


def test_selftest_workspace_creation_failure_is_explicit(tmp_path, monkeypatch):
    def fail_create(**kwargs):
        raise PermissionError("private denied")

    monkeypatch.setattr(d, "TemporaryDirectory", fail_create)
    report = d.collect_diagnostics(tmp_path, check_recording=True)
    assert check(report, "recording")["status"] == "error"
    assert not report["healthy"]
    assert "permissions" in check(report, "recording")["message"]


def test_selftest_cleanup_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    real_directory = d.TemporaryDirectory

    class FailedCleanup:
        def __init__(self, **kwargs):
            self.directory = real_directory(**kwargs)

        def __enter__(self):
            return self.directory.__enter__()

        def __exit__(self, *args):
            self.directory.__exit__(*args)
            raise OSError("private cleanup error")

    monkeypatch.setattr(d, "TemporaryDirectory", FailedCleanup)
    report = d.collect_diagnostics(tmp_path, check_recording=True)
    assert not report["healthy"]
    assert check(report, "recording")["status"] == "error"
    assert "filesystem error" in check(report, "recording")["message"]
    assert list(tmp_path.iterdir()) == []
    assert "private" not in json.dumps(d.support_report(report))


@pytest.mark.parametrize(("exception", "message"), [
    ("PermissionError", "filesystem permissions"),
    ("OSError", "filesystem error"),
])
def test_child_filesystem_failures_keep_safe_actionable_categories(tmp_path, monkeypatch, exception, message):
    real_run = subprocess.run

    def broken_run(command, **kwargs):
        command = list(command)
        command[3] = (
            "from pathlib import Path; "
            f"Path.write_text = lambda *args, **kwargs: (_ for _ in ()).throw({exception}('private')); "
            + command[3]
        )
        return real_run(command, **kwargs)

    monkeypatch.setattr(d.subprocess, "run", broken_run)
    report = d.collect_diagnostics(tmp_path, check_recording=True)
    safe = d.support_report(report)
    assert not safe["healthy"]
    assert message in check(safe, "recording")["message"]
    assert "private" not in json.dumps(safe)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("setup", [
    "import contextos_auditor._internal.audit_emit as a; "
    "a.load_events_with_stats = lambda path: ([], 0)",
    "import contextos_auditor._internal.audit_emit as a; "
    "a.load_events_with_stats = lambda path: ([], 1)",
    "import contextos_auditor._internal.shadow_kit as s; "
    "s.shadow_session = lambda events: {'actual': {'total_tokens': 0}}",
    "import contextos_auditor.report as r; r.render_terminal = lambda *args: '22 unrelated'",
    "import contextos_auditor.report as r; r.render_html = lambda *args, **kwargs: '<style>22</style>'",
    "from pathlib import Path; "
    "Path.write_text = lambda *args, **kwargs: (_ for _ in ()).throw(OSError('private disk failure'))",
])
def test_selftest_detects_actual_core_roundtrip_regressions(tmp_path, monkeypatch, setup):
    real_run = subprocess.run

    def broken_run(command, **kwargs):
        command = list(command)
        command[3] = setup + "; " + command[3]
        return real_run(command, **kwargs)

    monkeypatch.setattr(d.subprocess, "run", broken_run)
    report = d.collect_diagnostics(tmp_path, check_recording=True)
    assert not report["healthy"]
    assert check(report, "recording")["status"] == "error"
    assert list(tmp_path.iterdir()) == []
    assert "private" not in json.dumps(d.support_report(report))


def test_selftest_nonzero_process_exit_is_not_silent_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        d.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 2, "private-stdout", "private-stderr"),
    )
    report = d.collect_diagnostics(tmp_path, check_recording=True)
    assert check(report, "recording")["status"] == "error"
    assert check(report, "recording")["detail"] == "private-stderr"
    assert "private" not in json.dumps(d.support_report(report))
    assert list(tmp_path.iterdir()) == []


def test_environment_uses_scalar_runtime_information_not_host_details(tmp_path):
    report = d.collect_diagnostics(tmp_path)
    env = report["environment"]
    assert env["python_version"] == ".".join(str(v) for v in sys.version_info[:3])
    assert env["in_virtualenv"] is (sys.prefix != sys.base_prefix)
    assert env["audit_root"] == str(tmp_path)
    assert env["executable"] == sys.executable
    safe = d.support_report(report)
    assert safe["environment"]["platform"] in {"Linux", "Darwin", "Windows", "unknown"}
    assert all(type(value) in (str, bool) for value in safe["environment"].values())
