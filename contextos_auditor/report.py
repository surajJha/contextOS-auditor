"""Terminal + static-HTML rendering for a shadow_session() result. Shared by
the CLI's `watch`/`report` commands and `server.py`'s SSE live view."""

from __future__ import annotations

import html
from typing import Any


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
        f"-- {result['disclaimer']}",
    ]
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
<p class="disclaimer">{disclaimer}</p>
{footer}
</div>
</body>
</html>
"""


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
        disclaimer=html.escape(result["disclaimer"]),
        poll_seconds=poll_seconds,
    )
