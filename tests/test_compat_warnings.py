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


def test_c7_non_string_version_does_not_raise(monkeypatch):
    """BUG-C7: `crewai.__version__ = (0, 80, 0)` used to raise
    AttributeError straight out of every adapter's attach()."""
    import sys
    import types

    fake = types.ModuleType("crewai")
    fake.__version__ = (0, 80, 0)
    monkeypatch.setitem(sys.modules, "crewai", fake)
    compat._warned.discard("crewai")

    assert compat.installed_version("crewai") == "0.80.0"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        compat.check_compat("crewai")  # 0.80.0 is below the tested range
    assert len(caught) == 1


def test_c7_parse_version_handles_odd_shapes():
    assert compat._parse_version("1.2.0rc1") == (1, 2, 0)
    assert compat._parse_version("2.0.0-beta") == (2, 0, 0)
    assert compat._parse_version((0, 80, 0)) == (0, 80, 0)
    assert compat._parse_version(None) == (0, 0, 0)

    class _VersionInfo:
        def __str__(self):
            return "1.15.14"

    assert compat._parse_version(_VersionInfo()) == (1, 15, 14)


def test_c7_check_compat_never_raises_even_if_lookup_explodes(monkeypatch):
    def boom(_fw):
        raise RuntimeError("SDK metadata is broken")

    monkeypatch.setattr(compat, "installed_version", boom)
    compat._warned.discard("crewai")
    compat.check_compat("crewai")  # must swallow: never cost the user their run
