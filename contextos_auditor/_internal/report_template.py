"""Session-page layout and theme binding, independent of report data."""

from contextos_auditor._internal.theme import css_root_block

_HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
{refresh_tag}
<title>Live Auditor -- {session_id}</title>
<style>
__THEME_ROOT__
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
.cta {{
  background: var(--panel);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  margin: 0 0 1.5rem;
  padding: 1.1rem 1.25rem;
}}
.cta p {{ margin: 0 0 0.6rem; font-size: 0.92rem; }}
.cta p:last-child {{ margin-bottom: 0; }}
.cta code {{
  font-family: var(--mono);
  background: #1c2436;
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  font-size: 0.85rem;
}}
.est {{
  font-family: var(--mono);
  font-size: 0.62rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #8b94a7;
  border: 1px solid #2a3346;
  border-radius: 999px;
  padding: 0.1rem 0.4rem;
  vertical-align: middle;
  margin-left: 0.4rem;
  font-weight: 500;
}}
th.sub {{
  padding-left: 1.4rem;
  font-weight: 400;
  color: #8b94a7;
}}
.dupes {{ margin: 1.25rem 0; }}
.dupes h2 {{ font-size: 0.95rem; margin: 0 0 0.5rem; }}
.dupes .math {{
  font-family: var(--mono);
  font-size: 0.8rem;
  color: #8b94a7;
}}
.offenders {{ margin: 1.25rem 0; }}
.offenders h2 {{ font-size: 0.95rem; margin: 0 0 0.5rem; }}
.chart {{
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: var(--radius);
  margin: 0 0 1.5rem;
  padding: 1rem 1.1rem 0.5rem;
}}
.chart h2 {{ font-size: 0.95rem; margin: 0 0 0.75rem; }}
.chart-legend {{
  font-family: var(--mono);
  font-size: 0.78rem;
  color: var(--text-dim);
  margin: 0.4rem 0 0.75rem;
}}
.chart-legend .swatch {{
  display: inline-block;
  width: 0.7em;
  height: 0.7em;
  border-radius: 2px;
  margin: 0 0.35em 0 0.9em;
  vertical-align: middle;
}}
.chart-legend .swatch:first-child {{ margin-left: 0; }}
.export {{
  display: inline-block;
  font-family: var(--mono);
  font-size: 0.82rem;
  color: var(--text);
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: 999px;
  padding: 0.45rem 1rem;
  text-decoration: none;
  margin: 0 0 1.5rem;
}}
.export:hover {{ border-color: var(--accent); color: var(--accent); }}
.state-panel {{
  background: var(--panel);
  border: 1px solid var(--panel-border);
  border-radius: var(--radius);
  margin: 0 0 1.5rem;
  padding: 1.25rem 1.4rem;
}}
.state-panel.success {{ border-color: var(--pos); }}
.state-panel h2 {{ margin: 0 0 0.5rem; font-size: 1.05rem; }}
.state-panel.success h2 {{ color: var(--pos); }}
.state-panel p {{ margin: 0 0 0.4rem; font-size: 0.9rem; color: var(--text-dim); }}
.state-panel code {{
  font-family: var(--mono);
  background: #1c2436;
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  font-size: 0.85rem;
  color: var(--text);
}}
.error-banner {{
  background: rgba(255, 92, 122, 0.1);
  border: 1px solid var(--neg);
  border-radius: var(--radius);
  color: var(--neg);
  margin: 0 0 1.5rem;
  padding: 0.85rem 1.1rem;
  font-family: var(--mono);
  font-size: 0.85rem;
}}
</style>
</head>
<body>
<div class="wrap">
<h1>Live Auditor</h1>
<p class="meta">session={session_id} &middot; framework={framework} &middot; model={model} &middot; status={status}</p>
{error_banner_html}
{main_html}
{token_accuracy_html}
<p class="disclaimer">{disclaimer}</p>
{cta_html}
{export_html}
{footer}
</div>
</body>
</html>
"""

# Bind once; doubled braces survive the renderer's later format() call.
_HTML_TEMPLATE = _HTML_TEMPLATE.replace(
    "__THEME_ROOT__", css_root_block(escape_braces=True)
)
