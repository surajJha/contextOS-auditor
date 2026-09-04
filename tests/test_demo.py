"""LNCH-006 / LNCH-032: the demo path and duplicate-context waste.

These two are tested together because the demo session is the only
fixture in the repo that exercises the full waste model end to end, and
because the demo is the first thing a stranger runs -- if it regresses,
the product's first impression regresses with it.
"""

from __future__ import annotations

from contextos_auditor._internal.audit_emit import load_events
from contextos_auditor._internal.shadow_kit import shadow_session
from contextos_auditor.cli import main
from contextos_auditor.demo import run_demo


def _demo_result(tmp_path):
    session_id = run_demo(tmp_path)
    events = load_events(tmp_path / session_id)
    return shadow_session(events)


def test_demo_runs_with_no_framework_sdk_and_no_network(tmp_path):
    session_id = run_demo(tmp_path)
    assert (tmp_path / session_id / "events.jsonl").is_file()
    assert (tmp_path / session_id / "session.json").is_file()


def test_demo_session_is_clearly_marked_synthetic(tmp_path):
    """A demo run must never be quotable as a real measurement."""
    session_id = run_demo(tmp_path)
    meta = (tmp_path / session_id / "session.json").read_text()
    assert "synthetic demo session" in meta
    assert session_id.startswith("demo-")


def test_demo_surfaces_both_waste_categories(tmp_path):
    result = _demo_result(tmp_path)
    kit = result["kit_estimate"]
    assert kit["write_waste_tokens"] > 0, "whole-file rewrites should be caught"
    assert kit["duplicate_context_waste_tokens"] > 0, "re-reads should be caught"
    assert kit["waste_tokens"] == kit["write_waste_tokens"] + kit["duplicate_context_waste_tokens"]


def test_demo_headline_is_material_but_not_absurd(tmp_path):
    """Guards both failure modes at once.

    Too low and the free auditor fails to justify the paid tool (the
    pre-LNCH-032 behaviour was +1.8%, ~30x below measured savings). Too
    high and we're overclaiming on a synthetic fixture.
    """
    result = _demo_result(tmp_path)
    assert 4.0 <= result["save_pct"] <= 40.0


def test_duplicate_read_waste_multiplies_by_turns_carried(tmp_path):
    """A re-read is billed on every turn after it lands, not just once."""
    result = _demo_result(tmp_path)
    dupes = result["duplicate_reads"]
    assert dupes, "the demo re-reads the same file twice"
    for d in dupes:
        assert d["turns_carried"] >= 1
        assert d["waste_tokens"] == d["tokens"] * d["turns_carried"]
    # The earlier duplicate is carried further, so it must cost more.
    first, second = sorted(dupes, key=lambda d: d["turn"])[:2]
    assert first["turns_carried"] > second["turns_carried"]


def test_a_first_read_is_never_counted_as_duplicate(tmp_path):
    result = _demo_result(tmp_path)
    dup_turns = {d["turn"] for d in result["duplicate_reads"]}
    assert 1 not in dup_turns and 2 not in dup_turns


def test_duplicate_waste_never_exceeds_observed_prompt_tokens(tmp_path):
    """The honesty clamp: we cannot claim more waste than was spent."""
    result = _demo_result(tmp_path)
    assert result["kit_estimate"]["duplicate_context_waste_tokens"] <= result["actual"]["prompt_tokens"]


def test_demo_cli_prints_a_populated_report(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main(["demo", "--audit-root", str(tmp_path / "audit")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "duplicate reads" in out
    assert "estimated" in out
    assert "contextos-auditor doctor" in out


def test_bare_invocation_points_at_the_demo_instead_of_an_argparse_error(capsys):
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "contextos-auditor demo" in out


def test_running_the_demo_twice_does_not_double_the_numbers(tmp_path):
    """Regression: the demo writes to a fixed session id, so a second run
    used to append to the first -- turns, waste and duplicate reads all
    silently doubled, which made the headline number fabricated."""
    root = tmp_path / "audit"
    first = _demo_result(root)
    second = _demo_result(root)

    assert len(second["turns"]) == len(first["turns"])
    assert second["kit_estimate"]["waste_tokens"] == first["kit_estimate"]["waste_tokens"]
    assert len(second["duplicate_reads"]) == len(first["duplicate_reads"])
    assert second["actual"]["prompt_tokens"] == first["actual"]["prompt_tokens"]
