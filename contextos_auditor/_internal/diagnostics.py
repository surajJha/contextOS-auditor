"""Local onboarding checks and an allowlisted, shareable support report.

Importability is not integration readiness. No real event contents are read.
The opt-in recorder test runs in an isolated interpreter with no inherited
environment, so export settings and tokenizer initialization cannot affect it.
"""

from __future__ import annotations

import json
import os
import platform
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from contextos_auditor import __version__
from contextos_auditor._internal.compat import _TESTED

FRAMEWORKS: dict[str, tuple[str, str, str]] = {
    "crewai": ("crewai", "crewai", "crewai"),
    "langgraph": ("langchain_core", "langgraph", "langgraph"),
    "openai-agents": ("agents", "openai-agents", "openai_agents"),
    "autogen": ("autogen_core", "autogen", "autogen"),
}

_RETRY = "Run contextos-auditor doctor again."
_VERIFY = (
    "Attach the documented adapter, run your own agent, then use "
    "contextos-auditor watch to verify captured usage."
)
_ROOT_ACTION = (
    "Choose --audit-root pointing to a directory you can read and write; "
    "check parent directory permissions."
)
_RECORD_ACTION = (
    "Check directory permissions and available disk space, then run "
    "contextos-auditor doctor --check-recording."
)

# The report serializer reconstructs messages and actions from this catalog;
# it never copies free-form SDK errors, versions, paths, or caller-supplied text.
_TEXT: dict[str, dict[str, tuple[str, str]]] = {
    "environment": {
        "ok": ("Python environment information collected.", ""),
    },
    "scope": {
        "limited": (
            "Diagnostics do not verify SDK integration or live provider capture.",
            _VERIFY,
        ),
    },
    "python:crewai": {
        "unsupported": (
            "CrewAI dependencies may not support Python 3.14 or newer, even when Auditor imports.",
            "Use a Python 3.10-3.13 virtual environment for CrewAI and reinstall "
            'with python -m pip install "contextos-auditor[crewai]".',
        ),
    },
    "audit-root": {
        "existing": (
            "Audit root is an accessible directory; write access is indicated, not yet exercised.",
            "Use contextos-auditor doctor --check-recording to exercise local recording.",
        ),
        "missing": (
            "Audit root does not exist; its nearest existing directory permits creation.",
            "The first recorded session will create the audit root.",
        ),
        "not-directory": (
            "Audit root or an existing parent is not a directory.",
            _ROOT_ACTION,
        ),
        "permission": ("Audit root or its parent is not accessible.", _ROOT_ACTION),
        "io": ("Audit root could not be inspected because of a filesystem error.", _ROOT_ACTION),
    },
    "sessions": {
        "present": (
            "Session event files are present; their contents and capture quality were not checked.",
            "Use contextos-auditor watch or contextos-auditor report to inspect your local session.",
        ),
        "empty": (
            "No session event files found. This is normal before the first recorded run.",
            _VERIFY + " For a synthetic example, run contextos-auditor demo.",
        ),
        "permission": ("Session discovery was blocked by directory permissions.", _ROOT_ACTION),
        "io": ("Session discovery failed because of a filesystem error.", _ROOT_ACTION),
        "blocked": ("Session discovery was skipped because the audit root is inaccessible.", _ROOT_ACTION),
    },
    "tracing:openai-agents": {
        "disabled": (
            "OpenAI Agents tracing is disabled; the tracing adapter cannot capture usage.",
            "Unset OPENAI_AGENTS_DISABLE_TRACING before starting your agent, "
            "attach the adapter, and verify a new recorded run.",
        ),
        "enabled": (
            "OpenAI Agents tracing is not disabled by OPENAI_AGENTS_DISABLE_TRACING. "
            "Per-run or programmatic tracing settings are not checked.",
            _VERIFY,
        ),
    },
    "recording": {
        "ok": (
            "Synthetic recorder self-test passed: emit, load, analysis, and terminal/HTML rendering. "
            "This is not proof of SDK integration or live capture.",
            _VERIFY,
        ),
        "failed": (
            "Synthetic recorder self-test failed; local recording or rendering did not complete.",
            _RECORD_ACTION,
        ),
        "permission": ("Synthetic recorder self-test was blocked by filesystem permissions.", _RECORD_ACTION),
        "io": ("Synthetic recorder self-test failed because of a filesystem error.", _RECORD_ACTION),
        "timeout": ("Synthetic recorder self-test timed out.", _RECORD_ACTION),
        "blocked": ("Synthetic recorder self-test could not run because the audit root is inaccessible.", _ROOT_ACTION),
    },
}
for _name, (_module, _extra, _compat) in FRAMEWORKS.items():
    _install = f'python -m pip install "contextos-auditor[{_extra}]"'
    _TEXT[f"sdk:{_name}"] = {
        "missing": ("Optional SDK is not installed in this Python environment.", f"Run {_install}."),
        "dependency": (
            "SDK import failed because a dependency is missing or incompatible.",
            f"Run python -m pip check; repair the environment with {_install}.",
        ),
        "broken": (
            "SDK import raised an initialization error.",
            f"Run python -m pip check; try {_install} in a clean virtual environment.",
        ),
        "timeout": (
            "SDK import probe timed out; importability could not be verified.",
            f"Run python -m pip check; retry {_install} in a clean virtual environment.",
        ),
        "output-limit": (
            "SDK import produced excessive output; the probe was stopped.",
            f"Run python -m pip check; retry {_install} in a clean virtual environment.",
        ),
        "probe-error": (
            "SDK import probe could not complete or returned an invalid result.",
            f"Check Python process permissions and run python -m pip check; retry {_install}.",
        ),
        "unknown": (
            "SDK imports, but its version is unknown. Compatibility cannot be assessed.",
            "Check the installed SDK version with python -m pip list. " + _VERIFY,
        ),
        "outside": (
            "SDK imports, but its version is outside the broad advisory supported range.",
            "Review SDK compatibility before changing versions. " + _VERIFY,
        ),
        "inside": (
            "SDK imports and its version is within the broad advisory supported range; "
            "this does not mean every version was tested or capture works.",
            _VERIFY,
        ),
    }

_STATUSES = frozenset({"ok", "warning", "error", "info"})
_INVALID_TEXT = ("Diagnostic result could not be validated.", _RETRY)
_VERSION = re.compile(r"[0-9]{1,4}(?:\.[0-9]{1,4}){1,2}")
_SDK_VERSION = re.compile(
    r"([0-9]{1,4})(?:\.([0-9]{1,4}))?(?:\.([0-9]{1,4}))?"
    r"(?:(?:a|b|rc)[0-9]{1,4}|\.post[0-9]{1,4}|\.dev[0-9]{1,4})?"
)
_SDK_TIMEOUT = 10
_SDK_OUTPUT_LIMIT = 256 * 1024

# A standalone worker avoids importing a second Auditor copy or injecting
# source directories ahead of the interpreter's installed SDKs. Environment,
# interpreter and explicit PYTHONPATH are inherited, but -c's implicit working
# directory is removed before imports. Only this plugin boundary catches
# arbitrary exceptions. Both native and Python output stay in child pipes.
_SDK_PROBE_CODE = r"""
def inspect_sdk(import_module, module):
    try:
        sdk = import_module(module)
        raw_version = getattr(sdk, "__version__", None)
    except Exception as exc:
        if isinstance(exc, ModuleNotFoundError) and exc.name == module:
            code = "missing"
        elif isinstance(exc, ImportError):
            code = "dependency"
        else:
            code = "broken"
        try:
            detail = str(exc)[:4096]
        except Exception:
            detail = "SDK exception detail is unavailable."
        return {"code": code, "detail": detail}
    version = None
    if type(raw_version) is str and len(raw_version) <= 64:
        version = raw_version
    elif type(raw_version) in (list, tuple) and 1 <= len(raw_version) <= 3:
        if all(type(part) is int and 0 <= part <= 9999 for part in raw_version):
            version = list(raw_version)
    return {"code": "imported", "version": version}

if __name__ == "__main__":
    import sys
    sys.path[:] = [path for path in sys.path if path not in ("", ".")]
    import importlib
    import json
    result = inspect_sdk(importlib.import_module, sys.argv[1])
    sys.__stdout__.write(sys.argv[2] + json.dumps(result))
    sys.__stdout__.flush()
"""


def _check(check_id: str, code: str, status: str, detail: str | None = None) -> dict[str, Any]:
    message, action = _TEXT[check_id][code]
    row: dict[str, Any] = {"id": check_id, "status": status, "message": message, "action": action}
    if detail:
        row["detail"] = detail
    return row


def _version_tuple(raw: object) -> tuple[int, int, int] | None:
    if type(raw) in (tuple, list):
        if not 1 <= len(raw) <= 3 or any(type(x) is not int or not 0 <= x <= 9999 for x in raw):
            return None
        return tuple(list(raw) + [0] * (3 - len(raw)))
    if type(raw) is not str or len(raw) > 64:
        return None
    match = _SDK_VERSION.fullmatch(raw)
    return tuple(int(x or 0) for x in match.groups()) if match else None


def _safe_sdk_version(raw: object) -> str | None:
    if _version_tuple(raw) is None:
        return None
    if type(raw) is str:
        return raw
    return ".".join(map(str, raw))


class _WindowsJob:
    """Own a suspended probe and every descendant before any Python code runs."""

    CREATE_SUSPENDED = 0x00000004

    def __init__(self) -> None:
        import ctypes as c

        class BasicLimits(c.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", c.c_int64),
                ("PerJobUserTimeLimit", c.c_int64),
                ("LimitFlags", c.c_uint32),
                ("MinimumWorkingSetSize", c.c_size_t),
                ("MaximumWorkingSetSize", c.c_size_t),
                ("ActiveProcessLimit", c.c_uint32),
                ("Affinity", c.c_size_t),
                ("PriorityClass", c.c_uint32),
                ("SchedulingClass", c.c_uint32),
            ]

        class ExtendedLimits(c.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", c.c_uint64 * 6),
                ("ProcessMemoryLimit", c.c_size_t),
                ("JobMemoryLimit", c.c_size_t),
                ("PeakProcessMemoryUsed", c.c_size_t),
                ("PeakJobMemoryUsed", c.c_size_t),
            ]

        class Accounting(c.Structure):
            _fields_ = [
                ("TotalUserTime", c.c_int64),
                ("TotalKernelTime", c.c_int64),
                ("ThisPeriodTotalUserTime", c.c_int64),
                ("ThisPeriodTotalKernelTime", c.c_int64),
                ("TotalPageFaultCount", c.c_uint32),
                ("TotalProcesses", c.c_uint32),
                ("ActiveProcesses", c.c_uint32),
                ("TotalTerminatedProcesses", c.c_uint32),
            ]

        class ThreadEntry(c.Structure):
            _fields_ = [
                ("dwSize", c.c_uint32), ("cntUsage", c.c_uint32),
                ("th32ThreadID", c.c_uint32), ("th32OwnerProcessID", c.c_uint32),
                ("tpBasePri", c.c_int32), ("tpDeltaPri", c.c_int32),
                ("dwFlags", c.c_uint32),
            ]

        self._c = c
        self._accounting = Accounting
        self._thread_entry = ThreadEntry
        self._kernel = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": (c.c_void_p, [c.c_void_p, c.c_wchar_p]),
            "SetInformationJobObject": (c.c_int, [c.c_void_p, c.c_int, c.c_void_p, c.c_uint32]),
            "AssignProcessToJobObject": (c.c_int, [c.c_void_p, c.c_void_p]),
            "TerminateJobObject": (c.c_int, [c.c_void_p, c.c_uint32]),
            "QueryInformationJobObject": (
                c.c_int, [c.c_void_p, c.c_int, c.c_void_p, c.c_uint32, c.c_void_p],
            ),
            "CloseHandle": (c.c_int, [c.c_void_p]),
            "CreateToolhelp32Snapshot": (c.c_void_p, [c.c_uint32, c.c_uint32]),
            "Thread32First": (c.c_int, [c.c_void_p, c.POINTER(ThreadEntry)]),
            "Thread32Next": (c.c_int, [c.c_void_p, c.POINTER(ThreadEntry)]),
            "OpenThread": (c.c_void_p, [c.c_uint32, c.c_int, c.c_uint32]),
            "GetProcessIdOfThread": (c.c_uint32, [c.c_void_p]),
            "ResumeThread": (c.c_uint32, [c.c_void_p]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self._kernel, name)
            function.restype = restype
            function.argtypes = argtypes
        # Unnamed, non-inheritable handle: no SDK or unrelated process owns
        # this job. Do not enable either breakaway flag.
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise c.WinError(c.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(
            self._handle, 9, c.byref(limits), c.sizeof(limits),
        ):
            error = c.WinError(c.get_last_error())
            self._kernel.CloseHandle(self._handle)
            self._handle = None
            raise error

    def attach_and_resume(self, process: subprocess.Popen) -> None:
        c, kernel = self._c, self._kernel
        if not kernel.AssignProcessToJobObject(self._handle, int(process._handle)):
            raise c.WinError(c.get_last_error())
        # Popen closes CreateProcess's primary-thread handle. Its process was
        # created suspended, so find that thread, verify ownership by handle,
        # and resume only after successful job assignment.
        snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # SNAPTHREAD
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = self._thread_entry()
            entry.dwSize = c.sizeof(entry)
            found = kernel.Thread32First(snapshot, c.byref(entry))
            while found:
                if entry.th32OwnerProcessID == process.pid:
                    # QUERY_LIMITED_INFORMATION | SUSPEND_RESUME
                    thread = kernel.OpenThread(0x0800 | 0x0002, False, entry.th32ThreadID)
                    if not thread:
                        raise c.WinError(c.get_last_error())
                    try:
                        if kernel.GetProcessIdOfThread(thread) != process.pid:
                            raise OSError("SDK probe thread ownership could not be verified.")
                        if kernel.ResumeThread(thread) != 1:
                            raise OSError("SDK probe thread was not singly suspended.")
                    finally:
                        kernel.CloseHandle(thread)
                    return
                entry.dwSize = c.sizeof(entry)
                found = kernel.Thread32Next(snapshot, c.byref(entry))
            raise OSError("SDK probe primary thread could not be found.")
        finally:
            kernel.CloseHandle(snapshot)

    def close(self) -> None:
        if self._handle is None:
            return
        c, kernel, handle = self._c, self._kernel, self._handle
        try:
            if not kernel.TerminateJobObject(handle, 1):
                raise c.WinError(c.get_last_error())
            deadline = time.monotonic() + 1
            while True:
                accounting = self._accounting()
                if not kernel.QueryInformationJobObject(
                    handle, 1, c.byref(accounting), c.sizeof(accounting), None,
                ):
                    raise c.WinError(c.get_last_error())
                if accounting.ActiveProcesses == 0:
                    break
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired("SDK job cleanup", 1)
                time.sleep(0.01)
        finally:
            self._handle = None
            if not kernel.CloseHandle(handle):
                raise c.WinError(c.get_last_error())


def _stop_sdk_probe(process: subprocess.Popen, job: _WindowsJob | None = None) -> None:
    try:
        if job is not None:
            job.close()
        elif os.name == "posix":
            try:
                # Also close pipes inherited by import-spawned child processes.
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    finally:
        # Also covers a suspended Windows probe whose job assignment failed.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)


def _probe_sdk(module: str) -> dict[str, Any]:
    """Bound SDK imports by elapsed time and captured bytes, without parent FD changes.

    Returns ``code`` plus optional ``version`` and local-only ``detail``.
    Tests and CLI integrations can stub this boundary without installing SDKs.
    """
    marker = "\ncontextos-sdk-probe-" + uuid.uuid4().hex + ":"
    job = None
    process = None
    windows = sys.platform == "win32"
    try:
        if windows:
            job = _WindowsJob()
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", _SDK_PROBE_CODE, module, marker],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, start_new_session=os.name == "posix" and not windows,
            creationflags=_WindowsJob.CREATE_SUSPENDED if windows else 0,
        )
        if job is not None:
            job.attach_and_resume(process)
    except OSError as exc:
        try:
            if process is not None:
                _stop_sdk_probe(process, job)
            elif job is not None:
                job.close()
        except (OSError, subprocess.TimeoutExpired) as cleanup_error:
            return {"code": "probe-error", "detail": str(cleanup_error)}
        finally:
            if process is not None:
                process.stdout.close()
                process.stderr.close()
        return {"code": "probe-error", "detail": str(exc)}
    output: list[bytes] = []
    overflow = threading.Event()

    def drain(stream, keep: bool) -> None:
        total = 0
        try:
            while chunk := stream.read(8192):
                total += len(chunk)
                if total > _SDK_OUTPUT_LIMIT:
                    overflow.set()
                    process.kill()
                    return
                if keep:
                    output.append(chunk)
        except (OSError, ValueError):
            # A timed-out process can leave a descendant holding a pipe open.
            # The owner's cleanup closes that pipe; the reader must then exit.
            return

    readers = [
        threading.Thread(target=drain, args=(process.stdout, True), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, False), daemon=True),
    ]
    for reader in readers:
        reader.start()
    failure = None
    try:
        process.wait(timeout=_SDK_TIMEOUT)
    except subprocess.TimeoutExpired:
        failure = {"code": "timeout"}
    except OSError as exc:
        failure = {"code": "probe-error", "detail": str(exc)}
    finally:
        try:
            _stop_sdk_probe(process, job)
        except subprocess.TimeoutExpired:
            failure = {"code": "timeout"}
        except OSError as exc:
            failure = {"code": "probe-error", "detail": str(exc)}
        finally:
            for reader in readers:
                reader.join(timeout=0.5)
            process.stdout.close()
            process.stderr.close()
    if failure:
        return failure
    if overflow.is_set():
        return {"code": "output-limit"}
    if process.returncode:
        return {"code": "probe-error", "detail": f"SDK probe exited with code {process.returncode}."}
    _, separator, payload = b"".join(output).rpartition(marker.encode("ascii"))
    if not separator:
        return {"code": "probe-error"}
    try:
        result, _ = json.JSONDecoder().raw_decode(payload.decode("utf-8", errors="replace"))
    except ValueError:
        return {"code": "probe-error"}
    if type(result) is not dict or type(result.get("code")) is not str or result["code"] not in {
        "imported", "missing", "dependency", "broken",
    }:
        return {"code": "probe-error"}
    probe: dict[str, Any] = {"code": result["code"], "version": result.get("version")}
    if type(result.get("detail")) is str:
        probe["detail"] = result["detail"][:4096]
    return probe


def _sdk_check(name: str, selected: bool) -> dict[str, Any]:
    module, _, compat_key = FRAMEWORKS[name]
    check_id = f"sdk:{name}"
    probe = _probe_sdk(module)
    code = probe["code"]
    if code != "imported":
        return _check(
            check_id, code, "info" if code == "missing" and not selected else "error",
            probe.get("detail"),
        )
    raw_version = probe.get("version")
    version = _version_tuple(raw_version)
    if version is None:
        return _check(check_id, "unknown", "warning")
    spec = _TESTED[compat_key]
    inside = spec.min_version <= version < spec.max_version_exclusive
    # Only normalized numeric components reach local detail. SDK objects'
    # __str__ methods and untrusted version suffixes are never evaluated.
    detail = (
        f"Version: {'.'.join(map(str, version))}; advisory range: "
        f"[{'.'.join(map(str, spec.min_version))}, "
        f"{'.'.join(map(str, spec.max_version_exclusive))})."
    )
    row = _check(check_id, "inside" if inside else "outside", "ok" if inside else "warning", detail)
    safe_version = _safe_sdk_version(raw_version)
    if safe_version is not None:
        row["version"] = safe_version
    return row


def _nearest_directory(root: Path) -> tuple[Path, bool]:
    current = root
    while True:
        try:
            mode = current.stat().st_mode
        except FileNotFoundError:
            # A dangling symlink cannot serve as a future directory.
            if current.is_symlink():
                raise NotADirectoryError(str(current)) from None
            parent = current.parent
            if parent == current:
                raise
            current = parent
            continue
        if not stat.S_ISDIR(mode):
            raise NotADirectoryError(str(current))
        if not os.access(current, os.R_OK | os.W_OK | os.X_OK):
            raise PermissionError(str(current))
        return current, current == root


def _session_count(root: Path) -> int:
    count = 0
    # scandir/stat, rather than glob/is_file, preserve permission errors.
    # Do not follow session symlinks or open any user event/metadata files.
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.is_dir(follow_symlinks=False):
                continue
            try:
                mode = (Path(entry.path) / "events.jsonl").stat(follow_symlinks=False).st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISREG(mode):
                count += 1
    return count


def _recording_roundtrip(root: Path) -> None:
    """Child-process entry point; never call in the user's agent interpreter."""
    from contextos_auditor._internal import tokens
    from contextos_auditor._internal.audit_emit import AuditSession, load_events_with_stats
    from contextos_auditor._internal.shadow_kit import shadow_session
    from contextos_auditor.report import render_html, render_terminal

    # This interpreter is disposable. Disable tokenizer initialization even
    # when tiktoken is installed; no mutation reaches the user's interpreter.
    tokens._ENC = False
    session = AuditSession(
        root, session_id="synthetic-recorder-check", model="synthetic",
        task="Synthetic recorder self-test, not live SDK capture.", framework="diagnostics",
    )
    usage = {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22}
    session.emit_turn(1, usage=usage, cost={}, tool_calls=[])
    session.finish(success=True)
    events, skipped = load_events_with_stats(session.dir)
    if skipped or len(events) != 1 or events[0].get("usage") != usage:
        raise ValueError("Synthetic usage did not survive recording and loading.")
    result = shadow_session(events)
    if any(result["actual"].get(key) != value for key, value in usage.items()):
        raise ValueError("Synthetic usage did not survive analysis.")
    meta = {"status": "finished", "framework": "diagnostics", "model": "synthetic"}
    terminal = render_terminal("synthetic-recorder-check", meta, result)
    rendered_html = render_html("synthetic-recorder-check", meta, result, 0, auto_refresh=False)
    if (
        "actual:  prompt=17 completion=5 total=22 " not in terminal
        or "<th>actual total tokens</th><td>22</td>" not in rendered_html
    ):
        raise ValueError("Synthetic total usage was not rendered.")
    (session.dir / "report.txt").write_text(terminal, encoding="utf-8")
    (session.dir / "report.html").write_text(rendered_html, encoding="utf-8")


def _recording_main(directory: str) -> int:
    try:
        _recording_roundtrip(Path(directory))
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 70
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 71
    return 0


def _recording_check(ancestor: Path) -> dict[str, Any]:
    try:
        with TemporaryDirectory(prefix=".contextos-auditor-check-", dir=ancestor) as directory:
            # No inherited export/provider/tokenizer settings or credentials.
            # Windows Path.home() requires USERPROFILE. Point both platforms'
            # home lookup at this disposable workspace, never the user's home.
            env = {
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "HOME": directory,
                "USERPROFILE": directory,
            }
            if sys.platform == "win32" and os.environ.get("SystemRoot"):
                env["SystemRoot"] = os.environ["SystemRoot"]
            result = subprocess.run(
                [
                    sys.executable, "-BS", "-c",
                    "import sys; "
                    "from contextos_auditor._internal.diagnostics import _recording_main; "
                    "sys.exit(_recording_main(sys.argv[1]))",
                    directory,
                ],
                env=env, capture_output=True, text=True, timeout=30, check=False,
            )
            if result.returncode:
                code = {70: "permission", 71: "io"}.get(result.returncode, "failed")
                return _check("recording", code, "error", result.stderr)
        return _check("recording", "ok", "ok")
    except PermissionError as exc:
        return _check("recording", "permission", "error", str(exc))
    except OSError as exc:
        return _check("recording", "io", "error", str(exc))
    except subprocess.TimeoutExpired:
        return _check("recording", "timeout", "error")


def collect_diagnostics(
    audit_root: Path, framework: str | None = None, check_recording: bool = False,
) -> dict[str, Any]:
    """Inspect local prerequisites, optionally exercising the synthetic recorder.

    ``healthy`` means no blocking diagnostic errors, NOT ready to attach.
    Missing/broken unselected optional SDKs remain informational/advisory.
    ``detail`` and local environment paths must not be included in shared JSON;
    callers should use :func:`support_report` for that output.
    """
    if framework is not None and framework not in FRAMEWORKS:
        raise ValueError("Unknown framework; choose one of: " + ", ".join(FRAMEWORKS))
    root = Path(audit_root).absolute()
    environment = {
        "auditor_version": __version__,
        "python_version": ".".join(str(part) for part in sys.version_info[:3]),
        "python_implementation": platform.python_implementation(),
        "platform": platform.system(),
        "in_virtualenv": sys.prefix != sys.base_prefix,
        "executable": sys.executable,
        "audit_root": str(root),
    }
    checks = [_check("environment", "ok", "info"), _check("scope", "limited", "info")]
    for name in ([framework] if framework else FRAMEWORKS):
        checks.append(_sdk_check(name, framework is not None))
        if name == "crewai" and sys.version_info[:2] >= (3, 14):
            checks.append(_check("python:crewai", "unsupported", "warning"))
    if framework == "openai-agents":
        disabled = os.environ.get("OPENAI_AGENTS_DISABLE_TRACING", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
        checks.append(_check(
            "tracing:openai-agents", "disabled" if disabled else "enabled",
            "error" if disabled else "info",
        ))
    ancestor = None
    exists = False
    try:
        ancestor, exists = _nearest_directory(root)
        checks.append(_check("audit-root", "existing" if exists else "missing", "ok" if exists else "info"))
    except NotADirectoryError as exc:
        checks.append(_check("audit-root", "not-directory", "error", str(exc)))
    except PermissionError as exc:
        checks.append(_check("audit-root", "permission", "error", str(exc)))
    except OSError as exc:
        checks.append(_check("audit-root", "io", "error", str(exc)))
    if ancestor is None:
        checks.append(_check("sessions", "blocked", "info"))
    else:
        try:
            count = _session_count(root) if exists else 0
            checks.append(_check("sessions", "present" if count else "empty", "info"))
        except PermissionError as exc:
            checks.append(_check("sessions", "permission", "error", str(exc)))
        except OSError as exc:
            checks.append(_check("sessions", "io", "error", str(exc)))
    if check_recording:
        checks.append(
            _recording_check(ancestor) if ancestor is not None
            else _check("recording", "blocked", "error")
        )
    return {
        "schema_version": 1, "environment": environment, "checks": checks,
        "healthy": not any(
            check["status"] == "error"
            and not (framework is None and check["id"].startswith("sdk:"))
            for check in checks
        ),
    }


def support_report(report: dict[str, Any]) -> dict[str, Any]:
    """Construct a safe report from approved scalar values and fixed text only.

    Unknown fields are dropped, even when nested. An altered known check is
    replaced with a fixed error, never echoed. No regex-based redaction of
    arbitrary diagnostic text is involved. SDK version fields are optional
    and must match the entire numeric release/prerelease grammar; local version
    suffixes, whitespace and arbitrary SDK objects are never exported.
    """
    source = report.get("environment", {})
    if type(source) is not dict:
        source = {}
    environment: dict[str, Any] = {}
    for key in ("auditor_version", "python_version"):
        value = source.get(key)
        environment[key] = value if type(value) is str and _VERSION.fullmatch(value) else "unknown"
    for key, allowed in (
        ("python_implementation", {"CPython", "PyPy", "GraalVM", "Jython", "IronPython"}),
        ("platform", {"Linux", "Darwin", "Windows", "FreeBSD", "OpenBSD", "NetBSD", "Java"}),
    ):
        value = source.get(key)
        environment[key] = value if type(value) is str and value in allowed else "unknown"
    value = source.get("in_virtualenv")
    environment["in_virtualenv"] = value if type(value) is bool else None
    checks = []
    valid = True
    rows = report.get("checks", [])
    for row in rows if type(rows) is list else []:
        if type(row) is not dict:
            continue
        check_id = row.get("id")
        if type(check_id) is not str or check_id not in _TEXT:
            continue
        status = row.get("status")
        message = row.get("message")
        text = next(
            (text for text in _TEXT[check_id].values() if type(message) is str and text[0] == message),
            None,
        )
        if text is None or type(status) is not str or status not in _STATUSES:
            text, status = _INVALID_TEXT, "error"
            valid = False
        safe_row = {"id": check_id, "status": status, "message": text[0], "action": text[1]}
        if check_id.startswith("sdk:") and text != _INVALID_TEXT:
            version = _safe_sdk_version(row.get("version"))
            if version is not None:
                safe_row["version"] = version
        checks.append(safe_row)
    return {
        "schema_version": 1, "environment": environment, "checks": checks,
        # Optional broken SDK rows are errors without blocking overall health.
        "healthy": report.get("healthy") is True and valid,
    }
