"""Install an artifact in isolation and run copied regression tests against it."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("distribution", help="PyPI requirement or path to wheel/sdist")
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    package = Path(__file__).resolve().parents[1]
    results = args.results.resolve()
    results.mkdir(parents=True, exist_ok=True)
    target = args.distribution
    if Path(target).exists():
        target = str(Path(target).resolve())
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CONTEXTOS_REQUIRE_INSTALLED"] = "1"
    with tempfile.TemporaryDirectory(prefix="auditor-artifact-") as scratch:
        root = Path(scratch).resolve()
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                        target, "pytest>=7"], cwd=root, env=env, check=True)
        subprocess.run([str(python), "-m", "pip", "check"],
                       cwd=root, env=env, check=True)
        identity = subprocess.check_output(
            [str(python), "-I", "-c",
             "import contextos_auditor, importlib.metadata, json; "
             "print(json.dumps({'version': importlib.metadata.version('contextos-auditor'), "
             "'reported_version': contextos_auditor.__version__, "
             "'module': contextos_auditor.__file__}))"],
            cwd=root, env=env, text=True,
        )
        artifact = json.loads(identity)
        assert Path(artifact["module"]).resolve().is_relative_to(root / "venv"), artifact
        artifact.update(distribution=target, python=sys.version, platform=platform.platform())
        (results / "environment.json").write_text(
            json.dumps(artifact, indent=2) + "\n", encoding="utf-8",
        )
        # Preserve the tests' relative metadata/theme paths, but copy no package code.
        isolated_package = root / "repo" / "packages" / package.name
        isolated_package.mkdir(parents=True)
        shutil.copytree(package / "tests", isolated_package / "tests",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(package / "pyproject.toml", isolated_package / "pyproject.toml")
        site_source = package.parents[1] / "marketing-site"
        if not site_source.is_dir():
            site_source = package / "site"
        if site_source.is_dir():
            site = root / "repo" / "marketing-site"
            site.mkdir()
            shutil.copy2(site_source / "styles.css", site / "styles.css")
            shutil.copy2(site_source / "index.html", site / "index.html")
        run = subprocess.run(
            [str(python), "-I", "-m", "pytest", "tests", "-q", "-ra", "--tb=short",
             "--import-mode=importlib", "--junitxml", str(results / "junit.xml")],
            cwd=isolated_package, env=env, check=False,
        )
        return run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
