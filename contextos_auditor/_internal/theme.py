"""Single definition of the ContextOS visual theme used by the dashboard.

The local dashboard (`report.render_html`) and the marketing site are meant
to look like the same product. They used to express that by hand-copying
the same ten hex values into two `:root` blocks in two languages, which is
a guarantee of eventual drift: nothing failed when one of them changed, so
the dashboard would simply have started looking subtly wrong.

The palette is defined once, here, and the dashboard's stylesheet is
generated from it. `tests/test_theme_parity.py` then diffs these values
against the `:root` block in `marketing-site/styles.css`, so a change on
either side that isn't mirrored fails the suite instead of shipping.

Both surfaces use the same system-font stacks without font downloads.
Layout tokens (`--max-width` and friends) are page-specific and not shared.

No colour is computed or approximated here; these are literal values, so
the generated CSS is byte-predictable.
"""

from __future__ import annotations

#: Colours and geometry shared with `marketing-site/styles.css`. Every key
#: here MUST exist with an identical value in that file's `:root` block.
SHARED_TOKENS: dict[str, str] = {
    "bg": "#05070d",
    "bg-alt": "#0a0e18",
    "panel": "#0d1220",
    "panel-border": "#1c2436",
    "panel-border-soft": "#161d2c",
    "text": "#e7ebf5",
    "text-dim": "#8792a8",
    "text-dimmer": "#5b6478",
    "accent": "#7c5cff",
    "accent-2": "#35e2c4",
    "accent-warm": "#ff8a5c",
    "pos": "#35e2c4",
    "neg": "#ff5c7a",
    "na": "#444d5f",
    "radius": "14px",
}

#: System-font stacks, also mirrored by the marketing site -- see module
#: docstring. Kept in the same order as the site's declarations so the two
#: blocks read the same way side by side.
DASHBOARD_FONT_TOKENS: dict[str, str] = {
    "mono": '"SF Mono", Menlo, Consolas, "Roboto Mono", monospace',
    "sans": (
        "-apple-system, BlinkMacSystemFont, "
        '"Segoe UI", Roboto, Helvetica, Arial, sans-serif'
    ),
    "display": '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
}

#: Web font families the dashboard must never reference, because it cannot
#: fetch them. Asserted by the parity test.
WEB_FONT_FAMILIES = ("Space Grotesk", "Inter", "JetBrains Mono")


def dashboard_tokens() -> dict[str, str]:
    """Every custom property the dashboard's stylesheet defines."""
    return {**SHARED_TOKENS, **DASHBOARD_FONT_TOKENS}


def css_root_block(*, escape_braces: bool = False) -> str:
    """Render the `:root { ... }` block for the dashboard.

    `report._HTML_TEMPLATE` is consumed by `str.format`, where a literal
    brace has to be doubled. Passing `escape_braces=True` returns a block
    that survives that pass unchanged -- doing it here keeps the escaping
    next to the code that generates the braces rather than leaving a
    hand-doubled copy in the template.
    """
    lines = [f"  --{name}: {value};" for name, value in dashboard_tokens().items()]
    open_brace, close_brace = ("{{", "}}") if escape_braces else ("{", "}")
    return ":root " + open_brace + "\n" + "\n".join(lines) + "\n" + close_brace
