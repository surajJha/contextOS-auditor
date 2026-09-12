"""Terminal + static-HTML rendering for a shadow_session() result. Shared by
the CLI's `watch`/`report` commands and `server.py`'s SSE live view."""

from __future__ import annotations

import base64
import html
import json
from typing import Any

from contextos_auditor._internal.pricing import estimate_usd
from contextos_auditor._internal.report_history import (
    HISTORY_DISCLAIMER as HISTORY_DISCLAIMER,
    HISTORY_EMPTY_MARKER as HISTORY_EMPTY_MARKER,
    _HISTORY_TEMPLATE as _HISTORY_TEMPLATE,
    _history_table_html as _history_table_html,
    _history_trends_html as _history_trends_html,
    _sparkline_svg as _sparkline_svg,
    render_history_html as render_history_html,
)
from contextos_auditor._internal.report_template import _HTML_TEMPLATE as _HTML_TEMPLATE

#: LNCH-017 marker strings. Tests and the renderers below both key off
#: these exact substrings, so keep them as named constants rather than
#: inlined text that could drift apart.
NO_TURNS_MARKER = "waiting for the first turn"
NO_WASTE_MARKER = "no waste detected in this session"
SESSION_ERROR_MARKER = "SESSION ERROR"
#: BUG-F-002/C5 marker: shown whenever `load_events` had to skip lines it
#: could not parse, so a truncated session can never look like a complete
#: one. The count is carried on `meta["unreadable_lines"]`, injected by
#: `cli.snapshot()` rather than read from session.json.
UNREADABLE_LINES_MARKER = "unreadable lines skipped"


#: BUG-C8 marker: whether the token counts shown are real BPE counts or the
#: chars/4 fallback. The signal existed in `_internal/tokens.py` but was
#: never rendered anywhere, so a session counted with the heuristic looked
#: exactly as authoritative as an exactly-counted one.
TOKEN_ACCURACY_MARKER = "token counts:"


def _token_accuracy_note() -> str:
    """Explain local text-counting accuracy separately from provider usage."""
    try:
        from contextos_auditor._internal.tokens import accuracy_note

        return accuracy_note() or ""
    except Exception:
        return ""


def _unreadable_lines(meta: dict[str, Any]) -> int:
    try:
        return max(0, int(meta.get("unreadable_lines") or 0))
    except (TypeError, ValueError):
        return 0


def _unreadable_note(meta: dict[str, Any]) -> str:
    """BUG-F-002/C5: honesty requirement -- the numbers below are computed
    from fewer records than the file contains, and the reader must know."""
    n = _unreadable_lines(meta)
    if not n:
        return ""
    return (
        f"!! {n} {UNREADABLE_LINES_MARKER} in events.jsonl -- the totals below are "
        "incomplete (a torn final line is normal while a session is still running)"
    )


def _build_trace(result: dict[str, Any]) -> list[dict[str, Any]]:
    """AUD-013: fold `shadow_session()`'s flat `turns`/`writes` lists into a
    nested turn -> tool-call span tree, so a multi-turn run reads as a trace
    instead of only a bottom-line totals table. Every field here already
    exists in `result` -- this just re-shapes it, no new data collected."""
    waste_by_turn: dict[Any, list[dict[str, Any]]] = {}
    for w in result.get("writes") or []:
        waste_by_turn.setdefault(w.get("turn"), []).append(w)

    trace = []
    for t in result.get("turns") or []:
        turn_no = t.get("turn")
        spans = []
        for tool_name in t.get("tools") or []:
            spans.append({"name": tool_name})
        writes_here = waste_by_turn.get(turn_no) or []
        trace.append(
            {
                "turn": turn_no,
                "prompt_tokens": t.get("prompt_tokens", 0),
                "completion_tokens": t.get("completion_tokens", 0),
                "total_tokens": t.get("total_tokens", 0),
                "spans": spans,
                "writes": writes_here,
            }
        )
    return trace


def _build_tool_breakdown(result: dict[str, Any]) -> list[dict[str, Any]]:
    """LNCH-012: aggregate tool calls by tool name across the whole
    session -- call count, tokens attributable to that tool's waste, and
    that waste's share of the session's total recoverable waste. Sorted
    by attributable cost descending (ties broken by call count, then
    name, so output is stable across runs).

    Waste is only attributable where `shadow_session()` already ties it
    to a tool: whole-file rewrites to `write_file`, duplicate re-reads to
    `read_file`. Every other tool call still gets a row (count matters on
    its own -- a tool called 50 times for zero waste is still worth
    seeing) but with 0 attributable tokens."""
    counts: dict[str, int] = {}
    for t in result.get("turns") or []:
        for tool_name in t.get("tools") or []:
            if not tool_name:
                continue
            counts[tool_name] = counts.get(tool_name, 0) + 1

    waste_by_tool: dict[str, int] = {}
    for w in result.get("writes") or []:
        waste_by_tool["write_file"] = waste_by_tool.get("write_file", 0) + int(
            w.get("waste_tokens", 0)
        )
    for d in result.get("duplicate_reads") or []:
        waste_by_tool["read_file"] = waste_by_tool.get("read_file", 0) + int(
            d.get("waste_tokens", 0)
        )
    # A tool can rack up attributable waste without ever appearing in a
    # turn's `tools` list in some synthetic/partial fixtures -- still
    # give it a row rather than silently dropping tokens off the table.
    for name in waste_by_tool:
        counts.setdefault(name, 0)

    total_waste = sum(waste_by_tool.values())
    rows = []
    for name, count in counts.items():
        tokens = waste_by_tool.get(name, 0)
        share = (tokens / total_waste * 100.0) if total_waste else 0.0
        rows.append({"tool": name, "count": count, "tokens": tokens, "share": share})
    rows.sort(key=lambda r: (-r["tokens"], -r["count"], r["tool"]))
    return rows


def _usd_line(result: dict[str, Any]) -> str | None:
    usd = result["actual"].get("estimated_usd")
    if usd is None:
        return None
    pricing = result.get("pricing", {})
    return (
        f"estimated cost: ${usd:.4f} (list price as of {pricing.get('snapshot_date', '?')}, "
        f"not your negotiated rate -- see {pricing.get('source', '?')})"
    )


OPTIMISER_CONTACT_EMAIL = "skj48817@gmail.com"


def _turn_label(turn: Any) -> str:
    """BUG-R6: a duplicate read whose originating turn was never recorded
    used to render the literal string "turn None", which reads as a crash.
    An unknown turn is a real state (the tool call arrived before any LLM
    turn was closed), so name it honestly instead."""
    if turn is None or turn == "":
        return "?"
    return str(turn)


def _cta_usd_line(result: dict[str, Any], model: str | None = None) -> str | None:
    """AUD-017: the auditor's finding is the sales pitch for the paid
    `contextos-optimiser` package (see architecture.md's "coupled by
    design" note) -- but until now that pitch was never actually rendered
    anywhere a user would see it. Only fires when there's a genuine, real
    finding (waste_tokens > 0), so a clean session shows no CTA at all.

    BUG-R2: this used to compute `usd * (save_pct / 100)`, i.e. it scaled a
    *dollar* total by a *token-share* percentage. Those are not
    interchangeable: input and output tokens are priced differently (often
    4-8x apart), and the waste this tool finds is almost entirely duplicated
    *prompt* context. Scaling the blended total therefore overstated the
    dollar saving on every session whose output tokens cost more than its
    input tokens -- which is nearly all of them. We now price the wasted
    tokens directly, at the input rate, as the input tokens they actually
    are. If the model has no dated price, we say nothing rather than guess.
    """
    kit_est = result["kit_estimate"]
    waste_tokens = int(kit_est.get("waste_tokens", kit_est["write_waste_tokens"]) or 0)
    if waste_tokens <= 0:
        return None
    usd = result["actual"].get("estimated_usd")
    saved_usd = estimate_usd(model, waste_tokens, 0)
    if saved_usd is None:
        return None
    # BUG-R3: `~$0.0000` next to a positive percentage reads like a bug and
    # undermines every other number on the page. Below the display
    # precision, say so explicitly instead of rounding to a hard zero.
    saved_str = "<$0.0001" if saved_usd < 0.00005 else f"~${saved_usd:.4f}"
    if usd is None:
        return f"{saved_str} at list price"
    return f"{saved_str} of this session's ${usd:.4f} (list price)"


def _cta_lines(result: dict[str, Any], model: str | None = None) -> list[str] | None:
    kit_est = result["kit_estimate"]
    waste_tokens = kit_est.get("waste_tokens", kit_est["write_waste_tokens"])
    save_pct = result.get("save_pct", 0.0)
    if waste_tokens <= 0 or save_pct <= 0:
        return None
    usd_line = _cta_usd_line(result, model)
    lines = [
        "",
        f"Estimated opportunity: {save_pct:.1f}% ({waste_tokens} tokens"
        + (f", {usd_line}" if usd_line else "")
        + "). Not measured savings.",
        "contextos-optimiser provides replacement file tools to reduce",
        "supported waste. Wire them into your agent and measure the result;",
        "savings and task quality vary. Private access, with a 90-day trial.",
        f"To request access, email {OPTIMISER_CONTACT_EMAIL}",
    ]
    return lines


def _tool_breakdown_terminal_lines(result: dict[str, Any]) -> list[str]:
    """LNCH-012: per-tool call count / attributable waste tokens / share
    of session waste, sorted by cost descending."""
    rows = _build_tool_breakdown(result)
    if not rows:
        return []
    lines = ["", "top offenders (tool -> attributable waste):"]
    header = f"  {'tool':<20}{'calls':>8}{'tokens':>12}{'share':>9}"
    lines.append(header)
    for row in rows:
        lines.append(
            f"  {row['tool']:<20}{row['count']:>8}{row['tokens']:>12}{row['share']:>8.1f}%"
        )
    return lines


def render_terminal(session_id: str, meta: dict[str, Any], result: dict[str, Any]) -> str:
    actual = result["actual"]
    kit = result["kit_estimate"]
    status = meta.get("status", "unknown")
    n_turns = len(result["turns"])
    waste_tokens = kit.get("waste_tokens", kit["write_waste_tokens"])

    lines = [
        f"=== Live Auditor -- {session_id} ===",
        f"framework={meta.get('framework', 'unknown')} model={meta.get('model', 'unknown')} "
        f"status={status}",
    ]

    # LNCH-017c: an errored session is surfaced up front, not buried in the
    # one-word `status=` field a scrolling terminal would clip past.
    if status == "error":
        lines.append(f"!! {SESSION_ERROR_MARKER}: {meta.get('error') or '(no error detail recorded)'}")

    unreadable = _unreadable_note(meta)
    if unreadable:
        lines.append(unreadable)

    lines.append(f"turns so far: {n_turns}")

    # LNCH-017a: no turns yet is a distinct, actionable state -- not a
    # table of zeros that reads identically to "nothing went wrong".
    if n_turns == 0:
        lines += [
            "",
            f"{NO_TURNS_MARKER} -- no tool/LLM activity has been recorded for this "
            "session yet.",
            "If your agent is already running, give it a moment and re-run this "
            "command.",
            "If nothing shows up, check the attach wiring: `contextos-auditor doctor`.",
            "",
            f"-- {result['disclaimer']}",
        ]
        return "\n".join(lines)

    lines.append(
        f"actual:  prompt={actual['prompt_tokens']} completion={actual['completion_tokens']} "
        f"total={actual['total_tokens']} nano_aiu={actual['total_nano_aiu']}"
    )
    accuracy = _token_accuracy_note()
    if accuracy:
        lines.append(f"{TOKEN_ACCURACY_MARKER} {accuracy}")
    usd_line = _usd_line(result)
    lines.append(
        usd_line
        if usd_line
        else "estimated cost: n/a (no dated pricing available for this session's model)"
    )
    lines.append(
        f"levers that would fire: {', '.join(result['levers_fired']) or '(none observed yet)'}"
    )

    # LNCH-017b: turns happened, but nothing recoverable was found -- an
    # explicit success state instead of a wall of zeroed waste rows.
    if waste_tokens <= 0:
        lines += ["", f"{NO_WASTE_MARKER} -- nothing to recover here."]
        lines.append(f"-- {result['disclaimer']}")
        return "\n".join(lines)

    lines += [
        f"kit estimate: waste_tokens={waste_tokens} ({kit['basis']}-based, estimated)",
        f"  - rewriting whole files instead of hunks: {kit['write_waste_tokens']} tokens",
        f"  - re-reading content already in context:  {kit.get('duplicate_context_waste_tokens', 0)} tokens",
        f"estimated savings if Kit were attached: {result['save_pct']:+.1f}% (estimated)",
    ]
    lines += _tool_breakdown_terminal_lines(result)
    dupes = result.get("duplicate_reads") or []
    if dupes:
        lines.append("")
        lines.append("duplicate reads (content re-sent that was already in context):")
        for d in dupes:
            bound = " (bounded by recorded spend)" if d.get("clamped") else ""
            lines.append(
                f"  turn {d['turn']}: re-read {d['path']} ({d['tokens']} tokens), "
                f"carried through {d['turns_carried']} turns = {d['waste_tokens']} tokens{bound}"
            )
    trace = _build_trace(result)
    if trace:
        lines.append("")
        lines.append("trace (turn -> tool calls):")
        for span in trace:
            lines.append(
                f"  turn {_turn_label(span.get('turn'))}: {span['total_tokens']} tokens "
                f"(prompt={span['prompt_tokens']} completion={span['completion_tokens']})"
            )
            for tool in span["spans"]:
                lines.append(f"    +-- {tool['name']}")
            for w in span["writes"]:
                lines.append(
                    f"        L waste detected: write to {w['path']} "
                    f"could have been {w['waste_tokens']} tokens smaller with a hunk-based edit"
                )
    lines.append(f"-- {result['disclaimer']}")
    cta = _cta_lines(result, meta.get("model"))
    if cta:
        lines += cta
    return "\n".join(lines)


def _trace_html(result: dict[str, Any]) -> str:
    """AUD-013: nested turn -> tool-call trace, rendered as a native
    <details>/<summary> tree (no JS needed, works in the static --html
    export too)."""
    trace = _build_trace(result)
    if not trace:
        return ""
    turns_html = []
    for span in trace:
        spans_html = "".join(
            f'<li>{html.escape(str(t["name"]))}</li>' for t in span["spans"]
        )
        for w in span["writes"]:
            spans_html += (
                f'<li class="waste">waste detected: write to '
                f'{html.escape(str(w["path"]))} could have been '
                f'{w["waste_tokens"]} tokens smaller with a hunk-based edit</li>'
            )
        turns_html.append(
            f'<div class="trace-turn">'
            f'<div class="trace-turn-head">turn {html.escape(_turn_label(span.get("turn")))} '
            f'&middot; {span["total_tokens"]} tokens '
            f'(prompt={span["prompt_tokens"]} completion={span["completion_tokens"]})</div>'
            f'<ul class="trace-spans">{spans_html or "<li>(no tool calls)</li>"}</ul>'
            f"</div>"
        )
    return (
        '<details class="trace" open>'
        "<summary>Trace (turn &rarr; tool calls)</summary>"
        + "".join(turns_html)
        + "</details>"
    )


def _cta_html(result: dict[str, Any], model: str | None = None) -> str:
    lines = _cta_lines(result, model)
    if not lines:
        return ""
    kit_est = result["kit_estimate"]
    waste_tokens = kit_est.get("waste_tokens", kit_est["write_waste_tokens"])
    save_pct = result["save_pct"]
    usd_line = _cta_usd_line(result, model)
    body = f"Estimated opportunity: <strong>{save_pct:.1f}%</strong> ({waste_tokens} tokens"
    if usd_line:
        body += f", {html.escape(usd_line)}"
    body += "). Not measured savings."
    return (
        '<div class="cta">'
        f"<p>{body}</p>"
        "<p><code>contextos-optimiser</code> provides replacement file tools to reduce "
        "supported waste. Wire them into your agent and measure the result; "
        "savings and task quality vary. Private access, with a 90-day trial.</p>"
        f'<p>To request access, email <a href="mailto:{OPTIMISER_CONTACT_EMAIL}">'
        f"{OPTIMISER_CONTACT_EMAIL}</a></p>"
        "</div>"
    )


def _dupes_html(result: dict[str, Any]) -> str:
    """LNCH-011/LNCH-013: show duplicate-read waste *and* the arithmetic
    that produced it. A headline number the user can't reconstruct is a
    number they have no reason to believe."""
    dupes = result.get("duplicate_reads") or []
    if not dupes:
        return ""
    rows = []
    for d in dupes:
        bound = " (bounded by recorded spend)" if d.get("clamped") else ""
        rows.append(
            "<li>turn {turn}: re-read <code>{path}</code> &mdash; content already in context"
            '<div class="math">{tokens} tokens &times; {carried} turns carried = '
            "<strong>{waste}</strong> tokens{bound}</div></li>".format(
                turn=html.escape(_turn_label(d.get("turn"))),
                path=html.escape(str(d.get("path"))),
                tokens=html.escape(str(d.get("tokens"))),
                carried=html.escape(str(d.get("turns_carried"))),
                waste=html.escape(str(d.get("waste_tokens"))),
                bound=bound,
            )
        )
    return (
        '<div class="dupes"><h2>Duplicate context <span class="est">estimated</span></h2>'
        "<p class=\"disclaimer\">Content re-sent to the model that it was already "
        "carrying. Each duplicate is billed again in the prompt of every turn that "
        "follows it, so the cost is its size times the turns it was carried through. "
        "If your provider bills cached prefixes at a discount, treat this as an "
        "upper bound.</p><ul>" + "".join(rows) + "</ul></div>"
    )


def _tool_breakdown_html(result: dict[str, Any]) -> str:
    """LNCH-012: "top offenders" table -- call count / attributable waste
    tokens / share of session waste, per tool, sorted by cost descending."""
    rows = _build_tool_breakdown(result)
    if not rows:
        return ""
    body = "".join(
        "<tr><td>{tool}</td><td>{count}</td><td>{tokens}</td><td>{share:.1f}%</td></tr>".format(
            tool=html.escape(str(r["tool"])),
            count=r["count"],
            tokens=r["tokens"],
            share=r["share"],
        )
        for r in rows
    )
    return (
        '<div class="offenders"><h2>Top offenders (by attributable waste)</h2>'
        "<table><tr><th>tool</th><th>calls</th><th>waste tokens</th><th>share</th></tr>"
        + body
        + "</table></div>"
    )


def _tokens_svg(result: dict[str, Any]) -> str:
    """LNCH-015: hand-rolled inline SVG bar chart of prompt vs completion
    tokens per turn -- no chart library, works fully offline. Stacked bars
    (prompt on the bottom, completion on top) so a single glance shows both
    the per-turn total and its split. Degrades gracefully:
      - 0 turns: no chart at all (nothing meaningful to plot).
      - 1 turn: a single full-width bar.
      - 100+ turns: bars shrink to a hairline rather than overflowing or
        raising -- the SVG viewBox is fixed-width and scales via `width`.
    """
    turns = result.get("turns") or []
    if not turns:
        return ""
    n = len(turns)
    width, height = 640, 160
    pad_left, pad_bottom, pad_top, pad_right = 10, 20, 10, 10
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    # BUG-R7: the chart used prompt+completion while every trace row prints
    # `total_tokens`. Frameworks that report only a total (prompt and
    # completion both 0) therefore drew an empty chart next to rows full of
    # real numbers. Scale on whichever is larger so the two agree.
    max_total = max(
        max(
            int(t.get("prompt_tokens", 0)) + int(t.get("completion_tokens", 0)),
            int(t.get("total_tokens", 0)),
        )
        for t in turns
    ) or 1
    bar_w = plot_w / n
    gap = min(2.0, bar_w * 0.15)
    baseline_y = pad_top + plot_h

    bars = []
    for i, t in enumerate(turns):
        prompt = int(t.get("prompt_tokens", 0))
        completion = int(t.get("completion_tokens", 0))
        total = int(t.get("total_tokens", 0))
        turn_no = html.escape(str(t.get("turn", i + 1)))
        x = pad_left + i * bar_w
        w = max(0.5, bar_w - gap)
        if prompt + completion == 0 and total > 0:
            # BUG-R7: this framework reported a total but no split. Draw the
            # total as one honest bar rather than inventing a prompt /
            # completion breakdown we were never given.
            h = (total / max_total) * plot_h
            bars.append(
                f'<rect x="{x:.2f}" y="{baseline_y - h:.2f}" width="{w:.2f}" '
                f'height="{h:.2f}" fill="#7c5cff">'
                f"<title>turn {turn_no} total: {total} tokens "
                f"(prompt/completion split not reported)</title></rect>"
            )
            continue
        prompt_h = (prompt / max_total) * plot_h
        completion_h = (completion / max_total) * plot_h
        y_prompt = baseline_y - prompt_h
        y_completion = y_prompt - completion_h
        bars.append(
            f'<rect x="{x:.2f}" y="{y_prompt:.2f}" width="{w:.2f}" height="{prompt_h:.2f}" '
            f'fill="#7c5cff"><title>turn {turn_no} prompt: {prompt} tokens</title></rect>'
        )
        if completion_h > 0:
            bars.append(
                f'<rect x="{x:.2f}" y="{y_completion:.2f}" width="{w:.2f}" '
                f'height="{completion_h:.2f}" fill="#35e2c4">'
                f"<title>turn {turn_no} completion: {completion} tokens</title></rect>"
            )
    svg = (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'preserveAspectRatio="none" role="img" aria-label="tokens per turn">'
        f'<line x1="{pad_left}" y1="{baseline_y:.2f}" x2="{width - pad_right}" '
        f'y2="{baseline_y:.2f}" stroke="#1c2436" stroke-width="1" />'
        + "".join(bars)
        + "</svg>"
    )
    return (
        '<div class="chart"><h2>Tokens per turn</h2>'
        '<div class="chart-legend">'
        '<span class="swatch" style="background:#7c5cff"></span>prompt'
        '<span class="swatch" style="background:#35e2c4"></span>completion'
        "</div>" + svg + "</div>"
    )


def _export_button_html(session_id: str, result: dict[str, Any]) -> str:
    """LNCH-016: "Download JSON" -- the full `result` dict as a `data:`
    URI, base64-encoded so no further attribute-escaping is needed (the
    alphabet is `[A-Za-z0-9+/=]`, none of which are HTML-special). Built
    entirely in Python; no server round-trip, works from a `file://` URL."""
    payload = json.dumps(result, default=str)
    b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    filename = html.escape(f"{session_id}.json")
    return (
        f'<a class="export" href="data:application/json;charset=utf-8;base64,{b64}" '
        f'download="{filename}">&#8595; Download JSON</a>'
    )


def _error_banner_html(meta: dict[str, Any]) -> str:
    """LNCH-017c: an errored session must be visible without reading the
    small `status=` field in the meta line. BUG-F-002/C5 reuses the same
    banner slot for a "N unreadable lines skipped" warning."""
    banners = []
    if meta.get("status") == "error":
        error_detail = html.escape(str(meta.get("error") or "(no error detail recorded)"))
        banners.append(
            f'<div class="error-banner">&#9888; {SESSION_ERROR_MARKER}: {error_detail}</div>'
        )
    n = _unreadable_lines(meta)
    if n:
        banners.append(
            f'<div class="error-banner">&#9888; {n} {UNREADABLE_LINES_MARKER} in '
            "events.jsonl -- the numbers on this page are incomplete.</div>"
        )
    return "".join(banners)


def _main_html(result: dict[str, Any]) -> str:
    """Chooses one of four mutually exclusive body states (LNCH-017):
    no turns yet, turns with zero waste (success), or the full
    waste/trace/chart/tool-breakdown view. The CTA and error banner are
    assembled separately in `render_html` -- this only covers the part of
    the page that would otherwise be "a table of zeros"."""
    n_turns = len(result["turns"])
    kit = result["kit_estimate"]
    waste_tokens = kit.get("waste_tokens", kit["write_waste_tokens"])

    if n_turns == 0:
        return (
            '<div class="state-panel"><h2>Waiting&hellip;</h2>'
            f"<p>{NO_TURNS_MARKER} -- no tool/LLM activity has been recorded for this "
            "session yet.</p>"
            "<p>If your agent is already running, give it a moment and refresh. If "
            "nothing shows up, check the attach wiring: <code>contextos-auditor "
            "doctor</code>.</p></div>"
        )

    actual = result["actual"]
    usd = actual.get("estimated_usd")
    pricing = result.get("pricing", {})
    usd_display = (
        f"${usd:.4f} (list price as of {html.escape(str(pricing.get('snapshot_date', '?')))}, "
        "not your negotiated rate)"
        if usd is not None
        else "n/a (no dated pricing for this model)"
    )
    levers = html.escape(", ".join(result["levers_fired"]) or "(none observed yet)")

    if waste_tokens <= 0:
        return (
            '<div class="state-panel success"><h2>&#10003; No waste detected</h2>'
            f"<p>{NO_WASTE_MARKER} across {n_turns} turn(s) -- nothing to recover here.</p>"
            "<table>"
            f"<tr><th>turns so far</th><td>{n_turns}</td></tr>"
            f"<tr><th>actual prompt tokens</th><td>{actual['prompt_tokens']}</td></tr>"
            f"<tr><th>actual completion tokens</th><td>{actual['completion_tokens']}</td></tr>"
            f"<tr><th>actual total tokens</th><td>{actual['total_tokens']}</td></tr>"
            f"<tr><th>estimated cost <span class=\"est\">estimated</span></th><td>{usd_display}</td></tr>"
            f"<tr><th>levers that would fire</th><td>{levers}</td></tr>"
            "</table></div>"
            + _tokens_svg(result)
        )

    pct = result["save_pct"]
    pct_class = "pos" if pct >= 0 else "neg"
    parts = [
        '<p class="savings-label">Estimated savings if Kit were attached '
        '<span class="est">estimated</span></p>',
        f'<div class="pct {pct_class}">{pct:+.1f}%</div>',
        "<table>"
        f"<tr><th>turns so far</th><td>{n_turns}</td></tr>"
        f"<tr><th>actual prompt tokens</th><td>{actual['prompt_tokens']}</td></tr>"
        f"<tr><th>actual completion tokens</th><td>{actual['completion_tokens']}</td></tr>"
        f"<tr><th>actual total tokens</th><td>{actual['total_tokens']}</td></tr>"
        f"<tr><th>estimated cost <span class=\"est\">estimated</span></th><td>{usd_display}</td></tr>"
        f"<tr><th>total recoverable waste ({html.escape(kit['basis'])}-based) "
        f'<span class="est">estimated</span></th><td>{waste_tokens}</td></tr>'
        f'<tr><th class="sub">&#8226; whole-file rewrites instead of hunks</th>'
        f"<td>{kit['write_waste_tokens']}</td></tr>"
        f'<tr><th class="sub">&#8226; re-reading content already in context</th>'
        f"<td>{kit.get('duplicate_context_waste_tokens', 0)}</td></tr>"
        f"<tr><th>levers that would fire</th><td>{levers}</td></tr>"
        "</table>",
        _tool_breakdown_html(result),
        _tokens_svg(result),
        _dupes_html(result),
        _trace_html(result),
    ]
    return "".join(parts)


def _token_accuracy_html() -> str:
    """BUG-C8: same honesty label as the terminal report, rendered next to
    the numbers it qualifies. Empty when counts are exact."""
    note = _token_accuracy_note()
    if not note:
        return ""
    return (
        f'<p class="disclaimer">{html.escape(TOKEN_ACCURACY_MARKER)} '
        f"{html.escape(note)}</p>"
    )


def render_html(
    session_id: str,
    meta: dict[str, Any],
    result: dict[str, Any],
    poll_seconds: float,
    *,
    auto_refresh: bool = True,
) -> str:
    """`auto_refresh=True` (default) renders a meta-refresh tag and a footer
    saying so -- correct only when something is actually re-writing this
    file on each poll (`watch --html`).

    BUG-R4: this used to be the behaviour for *every* HTML write, including
    the one-shot exports produced by `report --html`, `demo --html` and
    `watch --serve --html`. Nothing rewrites those files, so the page
    silently reloaded identical content forever and the footer made a
    promise the file could not keep. Those callers now pass
    `auto_refresh=False`, as does `server.py`, whose SSE stream updates the
    page in place."""
    return _HTML_TEMPLATE.format(
        refresh_tag=f'<meta http-equiv="refresh" content="{poll_seconds:g}">' if auto_refresh else "",
        footer=f"<p><small>auto-refreshes every {poll_seconds:g}s</small></p>" if auto_refresh else "",
        session_id=html.escape(session_id),
        framework=html.escape(str(meta.get("framework", "unknown"))),
        model=html.escape(str(meta.get("model", "unknown"))),
        status=html.escape(str(meta.get("status", "unknown"))),
        error_banner_html=_error_banner_html(meta),
        main_html=_main_html(result),
        token_accuracy_html=_token_accuracy_html(),
        disclaimer=html.escape(result["disclaimer"]),
        cta_html=_cta_html(result, meta.get("model")),
        export_html=_export_button_html(session_id, result),
        poll_seconds=poll_seconds,
    )
