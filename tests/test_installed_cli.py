"""Exercise the CLI in subprocesses, without pytest's import-path helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(tmp_path, *args, encoding="utf-8"):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    bootstrap = (
        f"import sys; sys.stdout.reconfigure(encoding={encoding!r}, errors='strict'); "
        "from contextos_auditor.cli import main; raise SystemExit(main())"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", bootstrap, *args],
        cwd=tmp_path, env=env, capture_output=True, timeout=30, check=False,
    )


@pytest.fixture(autouse=True)
def _require_installed_distribution(tmp_path):
    result = _run(tmp_path, "--help")
    if (b"No module named 'contextos_auditor'" in result.stderr
            and os.environ.get("CONTEXTOS_REQUIRE_INSTALLED") != "1"):
        pytest.skip("requires an installed distribution; the artifact workflow runs these tests")
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def test_clean_install_demo_report_and_history(tmp_path):
    output = tmp_path / "reports with spaces" / "demo.html"
    demo = _run(tmp_path, "demo", "--html", str(output))
    assert demo.returncode == 0, demo.stderr
    assert b"synthetic" in demo.stdout
    assert b"estimated" in demo.stdout.lower()
    assert "<html" in output.read_text(encoding="utf-8")
    root = tmp_path / ".contextos" / "audit"
    sessions = list(root.glob("*/session.json"))
    assert len(sessions) == 1
    meta = json.loads(sessions[0].read_text(encoding="utf-8"))
    assert meta["status"] == "finished"
    report = _run(tmp_path, "report", meta["id"])
    assert report.returncode == 0, report.stderr
    assert b"duplicate reads" in report.stdout
    watch = _run(tmp_path, "watch", "--once")
    assert watch.returncode == 0, watch.stderr
    history = _run(tmp_path, "history", "--html", str(tmp_path / "history.html"))
    assert history.returncode == 0, history.stderr
    assert meta["id"].encode() in history.stdout
    again = _run(tmp_path, "demo")
    assert again.returncode == 0, again.stderr
    assert len(list(root.glob("*/session.json"))) == 1


def test_no_session_is_an_actionable_error(tmp_path):
    result = _run(tmp_path, "watch", "--once")
    assert result.returncode == 1
    assert b"doctor" in result.stdout
    assert b"Traceback" not in result.stderr


def test_console_entrypoint_is_packaged(tmp_path):
    scripts = Path(sys.executable).parent
    entrypoint = scripts / ("contextos-auditor.exe" if os.name == "nt" else "contextos-auditor")
    result = subprocess.run([str(entrypoint), "--help"], cwd=tmp_path,
                            capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert b"demo" in result.stdout


def test_cli_version_matches_installed_metadata(tmp_path):
    from importlib.metadata import version

    result = _run(tmp_path, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().strip() == f"contextos-auditor {version('contextos-auditor')}"


def test_unicode_paths_on_legacy_console(tmp_path):
    root = tmp_path / "audit \u65e5\u672c\u8a9e"
    result = _run(tmp_path, "demo", "--audit-root", str(root), encoding="cp1252")
    assert result.returncode == 0, result.stderr.decode("cp1252", errors="replace")
    assert list(root.glob("*/events.jsonl"))
