"""Every displayed waste row must fit the recorded spend it claims to explain."""

from contextos_auditor._internal.shadow_kit import shadow_session
from contextos_auditor.report import _build_tool_breakdown, render_html, render_terminal


def _turn(number, tools=(), *, tokens=100, synthetic=False):
    return {
        "kind": "turn", "turn": number, "usage": {"total_tokens": tokens},
        "tool_calls": list(tools), "synthetic": synthetic,
    }


def _read(path="a.py", content="body\n" * 100):
    return {"name": "read_file", "args": {"path": path}, "result_text": content}


def _write(content):
    return {"name": "write_file", "args": {"path": "a.py", "content": content}}


def test_write_rows_are_clamped_together_with_the_headline():
    body = "old\n" + "unchanged\n" * 1000
    result = shadow_session([
        _turn(1, [_read(content=body)]),
        _turn(2, [_write(body.replace("old", "new", 1))]),
        _turn(3, [_write(body)]),
    ])
    estimate = result["kit_estimate"]
    assert estimate["write_waste_tokens"] == 300
    assert sum(w["waste_tokens"] for w in result["writes"]) == 300
    assert all(w["clamped"] for w in result["writes"])
    assert sum(row["tokens"] for row in _build_tool_breakdown(result)) == 300
    assert result["save_pct"] == 100


def test_zero_usage_has_no_claimed_savings_even_for_large_rewrites():
    body = "content\n" * 100
    result = shadow_session([
        _turn(1, [_read(content=body), _write(body)], tokens=0, synthetic=True),
    ])
    assert result["writes"]
    assert result["kit_estimate"]["waste_tokens"] == 0
    assert result["writes"][0]["waste_tokens"] == 0
    assert result["save_pct"] == 0


def test_duplicate_seen_only_in_terminal_flush_has_no_billed_carry():
    result = shadow_session([
        _turn(1, [_read()]),
        _turn(2, [_read()], tokens=0, synthetic=True),
    ])
    assert result["duplicate_reads"][0]["turns_carried"] == 0
    assert result["duplicate_reads"][0]["waste_tokens"] == 0
    assert result["kit_estimate"]["waste_tokens"] == 0


def test_turn_number_gaps_and_zero_usage_do_not_invent_round_trips():
    result = shadow_session([
        _turn(1, [_read(content="body")], tokens=1000),
        _turn(10, [_read(content="body")], tokens=1000),
        _turn(20, tokens=0),
        _turn(100, tokens=1000),
    ])
    duplicate = result["duplicate_reads"][0]
    assert duplicate["turns_carried"] == 2
    assert duplicate["waste_tokens"] == duplicate["tokens"] * 2


def test_mixed_pricing_is_not_presented_as_a_fully_priced_session():
    priced = _turn(1)
    priced["cost"] = {"estimated_usd": 0.02}
    result = shadow_session([priced, _turn(2)])
    assert result["actual"]["estimated_usd"] is None


def test_clamped_duplicate_math_is_explicit_in_both_reports():
    result = shadow_session([
        _turn(1, [_read()], tokens=1), _turn(2, [_read()], tokens=1),
    ])
    assert result["duplicate_reads"][0]["clamped"]
    meta = {"status": "finished"}
    assert "bounded by recorded spend" in render_terminal("test", meta, result)
    assert "bounded by recorded spend" in render_html("test", meta, result, 1)
