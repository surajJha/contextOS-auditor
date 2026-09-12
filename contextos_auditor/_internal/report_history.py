"""Self-contained HTML history rendering and gap-aware sparklines."""

from __future__ import annotations

import html
from typing import Any

from contextos_auditor._internal.theme import css_root_block


def _sparkline_svg(
    values: list[float | None],
    labels: list[str],
    *,
    colour: str,
    fmt,
    width: int = 180,
    height: int = 34,
) -> str:
    """Render oldest-first values; missing prices break the line, not zero it."""
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(points) < 2:
        return '<span class="spark-na">n/a</span>'
    pad = 3.0
    plot_w = width - 2 * pad
    plot_h = height - 2 * pad
    lo = min(v for _, v in points)
    hi = max(v for _, v in points)
    span = hi - lo
    n = len(values)
    step = plot_w / (n - 1) if n > 1 else 0.0

    def y_of(v: float) -> float:
        if span == 0:
            return pad + plot_h / 2
        return pad + plot_h - ((v - lo) / span) * plot_h

    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for i, v in enumerate(values):
        if v is None:
            if len(current) > 1:
                segments.append(current)
            current = []
            continue
        current.append((pad + i * step, y_of(float(v))))
    if len(current) > 1:
        segments.append(current)

    paths = "".join(
        '<polyline fill="none" stroke="{c}" stroke-width="1.6" '
        'stroke-linejoin="round" stroke-linecap="round" points="{pts}"/>'.format(
            c=colour,
            pts=" ".join(f"{x:.2f},{y:.2f}" for x, y in seg),
        )
        for seg in segments
    )
    last_i, last_v = points[-1]
    dot = (
        f'<circle cx="{pad + last_i * step:.2f}" cy="{y_of(float(last_v)):.2f}" '
        f'r="2.4" fill="{colour}"/>'
    )
    titles = "".join(
        f"<title>{html.escape(labels[i])}: {html.escape(fmt(v))}</title>"
        for i, v in points[-1:]
    )
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" '
        f'height="{height}" role="img" preserveAspectRatio="none">'
        f"{titles}{paths}{dot}</svg>"
    )


HISTORY_EMPTY_MARKER = "no sessions recorded yet"

# History spans many billing bases, unlike a single session's disclaimer.
HISTORY_DISCLAIMER = (
    "Estimated opportunity only, aggregated across recorded sessions. "
    "Auditor does not change your bill. Savings apply when AgentCost Kit "
    "is attached (paid). Shadow cannot model trajectory changes."
)

_HISTORY_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Auditor history</title>
<style>
__THEME__
* { box-sizing: border-box; }
body {
  font-family: var(--sans);
  margin: 0;
  padding: 2.5rem 2rem;
  background: var(--bg);
  color: var(--text);
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 1080px; margin: 0 auto; }
h1 {
  font-family: var(--display);
  font-weight: 700;
  font-size: 1.9rem;
  letter-spacing: -0.01em;
  margin: 0 0 0.5rem;
}
h2 { font-size: 1.05rem; margin: 0 0 1rem; font-weight: 600; }
h3 {
  font-size: 0.78rem;
  margin: 0 0 0.6rem;
  font-weight: 500;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.meta { font-family: var(--mono); font-size: 0.82rem; color: var(--text-dim); margin: 0 0 2rem; }
.sparks, .history-panel {
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: var(--radius);
  padding: 1.4rem 1.5rem;
  margin: 0 0 1.5rem;
}
.spark-grid { display: flex; flex-wrap: wrap; gap: 1.5rem; }
.spark-card { flex: 1 1 200px; min-width: 200px; }
.spark { width: 100%; height: 34px; display: block; }
.spark-na { font-family: var(--mono); font-size: 0.78rem; color: var(--text-dimmer); }
.spark-range {
  font-family: var(--mono);
  font-size: 0.68rem;
  color: var(--text-dimmer);
  margin: 0.5rem 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
table { border-collapse: collapse; width: 100%; font-size: 0.84rem; }
th, td {
  text-align: left;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--panel-border-soft);
}
th {
  color: var(--text-dim);
  font-weight: 500;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
td.num { text-align: right; font-family: var(--mono); }
td.mono { font-family: var(--mono); font-size: 0.78rem; }
.pos { color: var(--pos); }
.neg { color: var(--neg); }
.warn { color: var(--accent-warm); margin-left: 0.4rem; cursor: help; }
.est {
  font-family: var(--mono);
  font-size: 0.62rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--accent-warm);
  border: 1px solid var(--accent-warm);
  border-radius: 999px;
  padding: 0.1rem 0.45rem;
  margin-left: 0.5rem;
  vertical-align: middle;
}
.hint, .notes { font-size: 0.78rem; color: var(--text-dim); }
.notes {
  border-left: 2px solid var(--accent-warm);
  padding: 0.4rem 0 0.4rem 1rem;
  margin: 1rem 0 0;
  list-style: none;
}
.notes li { margin: 0.2rem 0; font-family: var(--mono); font-size: 0.75rem; }
.state-panel { text-align: center; padding: 2rem 0; color: var(--text-dim); }
.disclaimer { font-size: 0.74rem; color: var(--text-dimmer); margin-top: 2rem; }
</style>
</head>
<body>
<div class="wrap">
<h1>Auditor history</h1>
<p class="meta">__N_SESSIONS__ session(s) &middot; __AUDIT_ROOT__</p>
__TRENDS__
<div class="history-panel">
<h2>Sessions <span class="est">estimated</span></h2>
__TABLE__
__MIXED__
__NOTES__
</div>
<p class="disclaimer">__DISCLAIMER__</p>
</div>
</body>
</html>
"""


def _history_trends_html(rows: list[dict]) -> str:
    """Reverse newest-first table rows for oldest-first sparklines."""
    if len(rows) < 2:
        return (
            '<p class="hint">Sparklines appear once you have at least two '
            "recorded sessions.</p>"
        )
    chrono = list(reversed(rows))
    labels = [str(r["session_id"]) for r in chrono]
    cards = [
        (
            "Total tokens",
            [float(r["total_tokens"]) for r in chrono],
            "#7c5cff",
            lambda v: f"{int(v)}",
        ),
        (
            "Estimated cost",
            [None if r["estimated_usd"] is None else float(r["estimated_usd"]) for r in chrono],
            "#ff8a5c",
            lambda v: f"${v:.4f}",
        ),
        (
            "Estimated savings",
            [float(r["save_pct"]) for r in chrono],
            "#35e2c4",
            lambda v: f"{v:+.1f}%",
        ),
    ]
    out = []
    for title, values, colour, fmt in cards:
        out.append(
            f'<div class="spark-card"><h3>{html.escape(title)}</h3>'
            + _sparkline_svg(values, labels, colour=colour, fmt=fmt)
            + f'<p class="spark-range">{html.escape(labels[0])} &rarr; '
            f"{html.escape(labels[-1])}</p></div>"
        )
    return (
        '<div class="sparks"><h2>Run over run <span class="est">estimated</span></h2>'
        '<div class="spark-grid">' + "".join(out) + "</div></div>"
    )


def _history_table_html(rows: list[dict]) -> str:
    if not rows:
        return f'<div class="state-panel"><h2>{HISTORY_EMPTY_MARKER}</h2></div>'
    body = []
    for r in rows:
        usd = r["estimated_usd"]
        usd_str = f"${usd:.4f}" if usd is not None else "n/a"
        pct = float(r["save_pct"])
        flag = (
            f'<span class="warn" title="{r["unreadable_lines"]} unreadable '
            'lines; this row\'s totals are incomplete">&#9888;</span>'
            if r["unreadable_lines"]
            else ""
        )
        body.append(
            "<tr>"
            f'<td class="mono">{html.escape(str(r["session_id"]))}{flag}</td>'
            f'<td>{html.escape(r["framework"])}</td>'
            f'<td>{html.escape(r["model"])}</td>'
            f'<td>{html.escape(r["status"])}</td>'
            f'<td class="num">{r["turns"]}</td>'
            f'<td class="num">{r["total_tokens"]}</td>'
            f'<td class="num">{usd_str}</td>'
            f'<td class="num {"pos" if pct >= 0 else "neg"}">{pct:+.1f}%</td>'
            "</tr>"
        )
    return (
        '<table class="history"><tr><th>session</th><th>framework</th>'
        "<th>model</th><th>status</th><th>turns</th><th>tokens</th>"
        "<th>cost</th><th>save%</th></tr>" + "".join(body) + "</table>"
    )


def render_history_html(
    rows: list[dict],
    skipped: list[str],
    unreadable_lines: int,
    audit_root: Any,
) -> str:
    """Render history with the terminal view's incomplete-data caveats."""
    notes = []
    for note in skipped:
        notes.append(
            f"<li>skipped unreadable session {html.escape(str(note))}</li>"
        )
    if unreadable_lines:
        notes.append(
            f"<li>{unreadable_lines} unreadable lines skipped across these "
            "sessions &mdash; their totals are incomplete</li>"
        )
    notes_html = (
        f'<ul class="notes">{"".join(notes)}</ul>' if notes else ""
    )
    models = {r["model"] for r in rows}
    mixed = (
        '<p class="hint">These sessions span more than one model '
        f"({html.escape(', '.join(sorted(models)))}), so the cost trend "
        "reflects both usage and price differences.</p>"
        if len(models) > 1
        else ""
    )
    return (
        _HISTORY_TEMPLATE.replace("__THEME__", css_root_block())
        .replace("__AUDIT_ROOT__", html.escape(str(audit_root)))
        .replace("__N_SESSIONS__", str(len(rows)))
        .replace("__TRENDS__", _history_trends_html(rows))
        .replace("__TABLE__", _history_table_html(rows))
        .replace("__NOTES__", notes_html)
        .replace("__MIXED__", mixed)
        .replace("__DISCLAIMER__", html.escape(HISTORY_DISCLAIMER))
    )
