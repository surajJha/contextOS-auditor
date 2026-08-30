"""Terminal + static-HTML rendering for a shadow_session() result. Shared by
the CLI's `watch`/`report` commands and `server.py`'s SSE live view."""

from __future__ import annotations

import html
from typing import Any


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
body {{ font-family: -apple-system, sans-serif; margin: 2rem; background: #fafafa; color: #1a1a1a; }}
.pct {{ font-size: 2.2rem; font-weight: 700; }}
.pct.pos {{ color: #2e7d32; }}
.pct.neg {{ color: #c62828; }}
table {{ border-collapse: collapse; margin: 1rem 0; }}
th, td {{ border: 1px solid #ddd; padding: 0.4rem 0.7rem; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
.disclaimer {{ font-size: 0.8rem; color: #666; max-width: 500px; }}
</style>
</head>
<body>
<h1>Live Auditor</h1>
<p>session={session_id} &middot; framework={framework} &middot; model={model} &middot; status={status}</p>
<p>Estimated savings if Kit were attached:</p>
<div class="pct {pct_class}">{pct_str}</div>
<table>
<tr><th>turns so far</th><td>{n_turns}</td></tr>
<tr><th>actual prompt tokens</th><td>{prompt_tokens}</td></tr>
<tr><th>actual completion tokens</th><td>{completion_tokens}</td></tr>
<tr><th>actual total tokens</th><td>{total_tokens}</td></tr>
<tr><th>write waste tokens ({basis}-based)</th><td>{waste_tokens}</td></tr>
<tr><th>levers that would fire</th><td>{levers}</td></tr>
</table>
<p class="disclaimer">{disclaimer}</p>
{footer}
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
        basis=html.escape(result["kit_estimate"]["basis"]),
        waste_tokens=result["kit_estimate"]["write_waste_tokens"],
        levers=html.escape(", ".join(result["levers_fired"]) or "(none observed yet)"),
        disclaimer=html.escape(result["disclaimer"]),
        poll_seconds=poll_seconds,
    )
