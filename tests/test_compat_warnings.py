"""AUD-007: verify the version-drift check actually warns exactly when it
should, and never blocks -- an out-of-range SDK version must still be
fully usable."""

from __future__ import annotations

import warnings

from contextos_auditor._internal import compat


def test_check_compat_is_silent_for_a_version_inside_the_tested_range(monkeypatch):
    monkeypatch.setattr(compat, "installed_version", lambda fw: "1.15.14")
    compat._warned.discard("crewai")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compat.check_compat("crewai")
    assert not caught


def test_check_compat_warns_once_for_a_version_outside_the_tested_range(monkeypatch):
    monkeypatch.setattr(compat, "installed_version", lambda fw: "3.0.0")
    compat._warned.discard("crewai")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compat.check_compat("crewai")
        compat.check_compat("crewai")  # second call: must not warn again
    assert len(caught) == 1
    assert "outside the range" in str(caught[0].message)


def test_check_compat_never_raises_for_an_unrecognized_framework():
    compat.check_compat("some-framework-not-in-the-table")  # must be a no-op


def test_installed_version_returns_none_when_sdk_not_importable(monkeypatch):
    monkeypatch.setattr("importlib.import_module", lambda name: (_ for _ in ()).throw(ImportError()))
    assert compat.installed_version("crewai") is None
