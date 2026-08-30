"""Terminal + static-HTML rendering for a shadow_session() result. Shared by
the CLI's `watch`/`report` commands and `server.py`'s SSE live view."""

from __future__ import annotations

import html
from typing import Any


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


def _usd_line(result: dict[str, Any]) -> str | None:
    usd = result["actual"].get("estimated_usd")
    if usd is None:
        return None
    pricing = result.get("pricing", {})
    return (
        f"estimated cost: ${usd:.4f} (list price as of {pricing.get('snapshot_date', '?')}, "
        f"not your negotiated rate -- see {pricing.get('source', '?')})"
    )


def render_terminal(session_id: str, meta: dict[str, Any], result: dict[str, Any]) -> str:
    actual = result["actual"]
    kit = result["kit_estimate"]
    lines = [
        f"=== Live Auditor -- {session_id} ===",
        f"framework={meta.get('framework', 'unknown')} model={meta.get('model', 'unknown')} "
        f"status={meta.get('status', 'unknown')}",
        f"turns so far: {len(result['turns'])}",
        f"actual:  prompt={actual['prompt_tokens']} completion={actual['completion_tokens']} "
        f"total={actual['total_tokens']} nano_aiu={actual['total_nano_aiu']}",
    ]
    usd_line = _usd_line(result)
    lines.append(
        usd_line
        if usd_line
        else "estimated cost: n/a (no dated pricing available for this session's model)"
    )
    lines += [
        f"kit estimate: waste_tokens={kit['write_waste_tokens']} ({kit['basis']}-based)",
        f"estimated savings if Kit were attached: {result['save_pct']:+.1f}%",
        f"levers that would fire: {', '.join(result['levers_fired']) or '(none observed yet)'}",
    ]
    trace = _build_trace(result)
    if trace:
        lines.append("")
        lines.append("trace (turn -> tool calls):")
        for span in trace:
            lines.append(
                f"  turn {span['turn']}: {span['total_tokens']} tokens "
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
    return "\n".join(lines)


_HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
{refresh_tag}
<title>Live Auditor -- {session_id}</title>
<style>
:root {{
  --bg: #05070d;
  --panel: #0d1220;
  --panel-border: #1c2436;
  --text: #e7ebf5;
  --text-dim: #8792a8;
  --text-dimmer: #5b6478;
  --accent: #7c5cff;
  --pos: #35e2c4;
  --neg: #ff5c7a;
  --radius: 14px;
  --mono: "SF Mono", Menlo, Consolas, "Roboto Mono", monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --display: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}}
* {{ box-sizing: border-box; }}
body {{
  font-family: var(--sans);
  margin: 0;
  padding: 2.5rem 2rem;
  background: var(--bg);
  color: var(--text);
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 720px; margin: 0 auto; }}
h1 {{
  font-family: var(--display);
  font-weight: 700;
  font-size: 1.9rem;
  letter-spacing: -0.01em;
  margin: 0 0 0.5rem;
  color: var(--text);
}}
.meta {{
  font-family: var(--mono);
  font-size: 0.85rem;
  color: var(--text-dim);
  margin: 0 0 1.75rem;
}}
.savings-label {{
  font-size: 0.85rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin: 0 0 0.35rem;
}}
.pct {{ font-family: var(--display); font-size: 2.6rem; font-weight: 700; margin: 0 0 1.75rem; }}
.pct.pos {{ color: var(--pos); }}
.pct.neg {{ color: var(--neg); }}
table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: var(--radius);
  overflow: hidden;
  margin: 0 0 1.5rem;
}}
th, td {{
  padding: 0.65rem 1rem;
  text-align: right;
  font-family: var(--mono);
  font-size: 0.9rem;
  border-bottom: 1px solid var(--panel-border);
}}
tr:last-child th, tr:last-child td {{ border-bottom: none; }}
th {{ text-align: left; font-weight: 500; color: var(--text-dim); }}
td {{ color: var(--text); }}
.disclaimer {{ font-size: 0.8rem; color: var(--text-dimmer); max-width: 560px; }}
a {{ color: var(--accent); }}
details.trace {{
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: var(--radius);
  margin: 0 0 1.5rem;
  padding: 0.9rem 1.1rem;
}}
details.trace > summary {{
  cursor: pointer;
  font-size: 0.85rem;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}}
.trace-turn {{ margin: 0.9rem 0 0 0; }}
.trace-turn-head {{
  font-family: var(--mono);
  font-size: 0.85rem;
  color: var(--text);
}}
.trace-spans {{ list-style: none; margin: 0.35rem 0 0; padding: 0; }}
.trace-spans li {{
  font-family: var(--mono);
  font-size: 0.82rem;
  color: var(--text-dim);
  padding: 0.15rem 0 0.15rem 1.2rem;
  border-left: 1px solid var(--panel-border);
  margin-left: 0.3rem;
}}
.trace-spans li.waste {{ color: var(--neg); }}
</style>
</head>
<body>
<div class="wrap">
<h1>Live Auditor</h1>
<p class="meta">session={session_id} &middot; framework={framework} &middot; model={model} &middot; status={status}</p>
<p class="savings-label">Estimated savings if Kit were attached</p>
<div class="pct {pct_class}">{pct_str}</div>
<table>
<tr><th>turns so far</th><td>{n_turns}</td></tr>
<tr><th>actual prompt tokens</th><td>{prompt_tokens}</td></tr>
<tr><th>actual completion tokens</th><td>{completion_tokens}</td></tr>
<tr><th>actual total tokens</th><td>{total_tokens}</td></tr>
<tr><th>estimated cost</th><td>{usd_display}</td></tr>
<tr><th>write waste tokens ({basis}-based)</th><td>{waste_tokens}</td></tr>
<tr><th>levers that would fire</th><td>{levers}</td></tr>
</table>
{trace_html}
<p class="disclaimer">{disclaimer}</p>
{footer}
</div>
</body>
</html>
"""


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
            f'<div class="trace-turn-head">turn {html.escape(str(span["turn"]))} '
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


def render_html(
    session_id: str,
    meta: dict[str, Any],
    result: dict[str, Any],
    poll_seconds: float,
    *,
    live: bool = False,
) -> str:
    """`live=False` (default) renders a static snapshot with a meta-refresh
    (used by `--html`, which re-writes this file to disk on each poll).
    `live=True` (used by `server.py`) omits the meta-refresh -- the SSE
    connection updates the page in place instead, so a periodic full
    reload would just be visual noise."""
    pct = result["save_pct"]
    usd = result["actual"].get("estimated_usd")
    pricing = result.get("pricing", {})
    usd_display = (
        f"${usd:.4f} (list price as of {html.escape(str(pricing.get('snapshot_date', '?')))}, not your negotiated rate)"
        if usd is not None
        else "n/a (no dated pricing for this model)"
    )
    return _HTML_TEMPLATE.format(
        refresh_tag="" if live else f'<meta http-equiv="refresh" content="{poll_seconds:g}">',
        footer="" if live else f"<p><small>auto-refreshes every {poll_seconds:g}s</small></p>",
        session_id=html.escape(session_id),
        framework=html.escape(str(meta.get("framework", "unknown"))),
        model=html.escape(str(meta.get("model", "unknown"))),
        status=html.escape(str(meta.get("status", "unknown"))),
        pct_class="pos" if pct >= 0 else "neg",
        pct_str=f"{pct:+.1f}%",
        n_turns=len(result["turns"]),
        prompt_tokens=result["actual"]["prompt_tokens"],
        completion_tokens=result["actual"]["completion_tokens"],
        total_tokens=result["actual"]["total_tokens"],
        usd_display=usd_display,
        basis=html.escape(result["kit_estimate"]["basis"]),
        waste_tokens=result["kit_estimate"]["write_waste_tokens"],
        levers=html.escape(", ".join(result["levers_fired"]) or "(none observed yet)"),
        trace_html=_trace_html(result),
        disclaimer=html.escape(result["disclaimer"]),
        poll_seconds=poll_seconds,
    )
