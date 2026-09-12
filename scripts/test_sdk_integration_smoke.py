"""Optional real-SDK checks, isolated from the unit suite's import stand-ins.

Run with an interpreter containing framework extras:
    python -m pytest scripts/test_sdk_integration_smoke.py
"""

import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("framework", "distribution"),
    [("crewai", "crewai"), ("langgraph", "langgraph"),
     ("openai_agents", "openai-agents"), ("autogen", "autogen-agentchat")],
)
def test_real_sdk_offline_orchestration(framework, distribution, tmp_path):
    try:
        importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(f"optional SDK {distribution} not installed")
    script = Path(__file__).with_name("sdk_integration_smoke.py")
    result = subprocess.run(
        [sys.executable, str(script), "--framework", framework, "--out-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "results.json").read_text())
    assert report["results"][framework]["status"] == "pass"
