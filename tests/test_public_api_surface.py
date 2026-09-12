"""Verifies the public import surface documented in the package README
actually works, standalone -- this is the contract external users depend
on. Each framework's SDK is `importorskip`'d since they're optional extras;
CI should run this in an environment with `[all]` installed to exercise
every branch, but the test suite itself must not hard-require any of them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def test_top_level_package_imports_with_zero_framework_deps():
    import contextos_auditor

    assert contextos_auditor.__version__
    assert hasattr(contextos_auditor, "shadow_session")
    assert hasattr(contextos_auditor, "load_events")


def test_public_version_matches_package_metadata():
    import contextos_auditor

    manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    versions = re.findall(
        r'^version\s*=\s*"([^"]+)"\s*$',
        manifest.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert versions == [contextos_auditor.__version__]


def test_crewai_public_api_surface():
    pytest.importorskip("crewai")
    from contextos_auditor.crewai import CrewAIAuditAdapter, attach

    assert callable(attach)
    assert CrewAIAuditAdapter


def test_langgraph_public_api_surface():
    pytest.importorskip("langchain_core")
    from contextos_auditor.langgraph import AuditorCallback

    assert AuditorCallback


def test_openai_agents_public_api_surface():
    pytest.importorskip("agents")
    from contextos_auditor.openai_agents import attach

    assert callable(attach)


def test_autogen_public_api_surface():
    pytest.importorskip("autogen_core")
    from contextos_auditor.autogen import audit_tool, new_session, wrap_client

    assert callable(new_session)
    assert callable(wrap_client)
    assert callable(audit_tool)


def test_cli_entrypoint_importable():
    from contextos_auditor.cli import build_parser, main

    parser = build_parser()
    assert parser.prog == "contextos-auditor"
    assert callable(main)
