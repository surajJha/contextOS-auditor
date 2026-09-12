"""LNCH-009: the dashboard and the marketing site must not drift apart.

`render_html` and `marketing-site/styles.css` are supposed to look like the
same product, but they used to express that by hand-copying ten hex values
into two `:root` blocks written in two different languages. Nothing failed
when one side changed, so the only way to notice was to put the two pages
side by side and squint -- which means in practice nobody would.

The palette now has one definition (`_internal/theme.py`) that the
dashboard's stylesheet is generated from, and these tests diff it against
the site's stylesheet so an unmirrored change fails the suite.

The site is a plain static file with no build step -- that is deliberate --
so it keeps literal values rather than importing anything from Python. This
test is the seam that keeps the two honest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from contextos_auditor._internal.theme import (
    DASHBOARD_FONT_TOKENS,
    SHARED_TOKENS,
    WEB_FONT_FAMILIES,
    css_root_block,
    dashboard_tokens,
)
from contextos_auditor.report import _HTML_TEMPLATE

#: Repo layout: packages/contextos-auditor-py/tests/ -> repo root.
SITE_CSS = Path(__file__).resolve().parents[3] / "marketing-site" / "styles.css"

_ROOT_BLOCK = re.compile(r":root\s*\{(.*?)\}", re.DOTALL)
_DECLARATION = re.compile(r"--([a-z0-9-]+)\s*:\s*([^;]+);")


def _parse_root(css: str) -> dict[str, str]:
    match = _ROOT_BLOCK.search(css)
    assert match, "no :root block found"
    return {
        name: value.strip() for name, value in _DECLARATION.findall(match.group(1))
    }


@pytest.fixture(scope="module")
def site_tokens() -> dict[str, str]:
    if not SITE_CSS.exists():
        pytest.skip(f"marketing site not present at {SITE_CSS} (installed package)")
    tokens = _parse_root(SITE_CSS.read_text(encoding="utf-8"))
    assert tokens, "parsed the site's :root block but found no custom properties"
    return tokens


# ------------------------------------------------------------- the parity


def test_every_shared_token_exists_on_the_site(site_tokens):
    missing = sorted(set(SHARED_TOKENS) - set(site_tokens))
    assert not missing, (
        f"theme.py declares {missing} as shared with the marketing site, but "
        f"{SITE_CSS.name} does not define them"
    )


def test_shared_token_values_are_identical(site_tokens):
    drifted = {
        name: (value, site_tokens[name])
        for name, value in SHARED_TOKENS.items()
        if name in site_tokens and site_tokens[name] != value
    }
    assert not drifted, (
        "dashboard and marketing site disagree on "
        f"{sorted(drifted)} (theme.py vs styles.css): {drifted}"
    )


def test_the_parse_is_not_vacuous(site_tokens):
    """A regex that silently matched nothing would make every assertion
    above pass while checking nothing at all."""
    assert len(site_tokens) >= len(SHARED_TOKENS)
    assert site_tokens.get("bg", "").startswith("#")


# ------------------------------------------- the intentional differences


def test_the_dashboard_never_references_a_web_font(site_tokens):
    """The dashboard is a local tool with zero dependencies and must render
    correctly with no network. The site's CDN fonts cannot leak into it."""
    generated = css_root_block()
    for family in WEB_FONT_FAMILIES:
        assert family not in generated, f"{family} cannot be fetched offline"
    assert "@import" not in _HTML_TEMPLATE
    assert "https://" not in _HTML_TEMPLATE.split("</style>")[0]


def test_the_site_uses_the_same_offline_font_stacks(site_tokens):
    for name, value in DASHBOARD_FONT_TOKENS.items():
        assert site_tokens[name] == value
    html = SITE_CSS.with_name("index.html").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_font_tokens_are_not_claimed_to_be_shared():
    assert not set(DASHBOARD_FONT_TOKENS) & set(SHARED_TOKENS)


# --------------------------------------------------- the generated block


def test_template_has_no_hand_written_root_block():
    """The whole point: there must be exactly one `:root`, and it must be
    the generated one."""
    assert _HTML_TEMPLATE.count(":root") == 1
    assert "__THEME_ROOT__" not in _HTML_TEMPLATE


def test_generated_block_defines_every_token_the_dashboard_uses():
    """A token used but never defined renders as an invalid value, which
    CSS silently ignores -- an invisible way to break the theme."""
    used = set(re.findall(r"var\(--([a-z0-9-]+)\)", _HTML_TEMPLATE))
    defined = set(dashboard_tokens())
    assert used <= defined, f"undefined custom properties: {sorted(used - defined)}"


def test_generated_block_survives_the_format_pass():
    """`_HTML_TEMPLATE` is consumed by str.format, so its braces are
    doubled. If the block were injected unescaped, format() would raise or
    silently eat the palette."""
    escaped = css_root_block(escape_braces=True)
    assert escaped.startswith(":root {{") and escaped.endswith("}}")
    assert escaped.replace("{{", "{").replace("}}", "}") == css_root_block()


def test_rendered_page_contains_the_real_values():
    from contextos_auditor._internal.shadow_kit import shadow_session
    from contextos_auditor.report import render_html

    page = render_html(
        "sess", {"framework": "crewai", "model": "gpt-4o", "status": "running"},
        shadow_session([]), 1.0,
    )
    assert f"--bg: {SHARED_TOKENS['bg']};" in page
    assert f"--accent: {SHARED_TOKENS['accent']};" in page
    # No unsubstituted marker and no leftover doubled braces in the output.
    assert "__THEME_ROOT__" not in page
    assert "{{" not in page and "}}" not in page
