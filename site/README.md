# ContextOS — marketing site

The product's main marketing pitch: a single-page, dark-theme landing page
covering both halves of the product (the free Agent Auditor, and the paid
ContextOS SDK), the token-saving levers, live-measured results, and
framework/model coverage.

## Design

Static HTML/CSS/JS, no build step, no framework, no bundler — mostly
consistent with the rest of this repo's "no build, opens via `file://`"
philosophy (`scripts/kit_demo/render_report.py`,
`scripts/kit_demo/live_auditor.py`), with **one deliberate exception**: this
page imports Google Fonts (Space Grotesk for display/headlines, Inter for
body text, JetBrains Mono for code/terminal) over a CDN link. Every other
part of the repo avoids external requests entirely; this is a conscious
trade-off made for a customer-facing landing page's visual quality, at the
user's explicit request — noted here so it doesn't read as an oversight.

Dark theme, gradient-glow accents, bento-grid feature layout, a
browser-chrome hero mockup of the live report, and an animated live-feeling
monitor of the Auditor working a real session. An abstract SVG mark (gapped
orbit ring + center node, in the site's purple→teal gradient) stands in for
a wordmark logo — no literal-letter monogram, evoking "OS/kernel" plus
"context orbiting".

`script.js` drives: reveal-on-scroll with per-card stagger, count-up/bar
animations, a scroll-progress bar, a cursor-spotlight glow (desktop/fine
pointer only), 3D tilt on cards (desktop/fine pointer only), active-nav-link
highlighting via `IntersectionObserver`, a cycling eyebrow-text rotator, the
`#auditorMonitor` live-session replay (typewriter log, ticking token
counter, savings gauge, finding chips), and a hand-rolled force-directed
graph (`#fwGraph`, canvas + custom 2D physics, no D3/vis dependency) that
plots every measured framework↔domain savings edge and lets nodes be
dragged via the Pointer Events API. All motion is gated behind
`prefers-reduced-motion` and pointer-type checks
(`matchMedia('(pointer: fine)')`) so touch devices and reduced-motion users
get the static, still-fully-functional version of the page.

**IP boundary:** the site shows *what* the Auditor finds and *what it's
worth* (e.g. "Duplicate call caught", "+X% savings") — it never shows *how*
the kit does it (no real function/API names, tool-profile IDs, or code
snippets). The Integration section is a generic 3-step flow, not a code
sample. Framework and model-family icons next to each badge/row are
deliberately abstract, hand-drawn glyphs in the site's own palette — not
recreations of the frameworks'/vendors' actual trademarked logos — so the
richer visual density doesn't imply endorsement or affiliation.

## Data honesty

Every number on the page is copied verbatim from a committed, generated
artifact — `docs/product/live-results-2026-08-30.md` (headline cards, the
domain × framework matrix) and `docs/product/framework-7domain-results-2026-08-22.md`
(the fuller N=5 per-domain table used in the hero mockup and results
section), plus `architecture.md` (tool-profile token counts) and the LLM
market-share figures supplied directly by the product owner. Nothing here
is invented copy or a fabricated testimonial/logo.

**When those source docs get fresh numbers (e.g. once `prod-009`'s N=15
re-sweep lands), the hero stats, headline cards, and matrix in `index.html`
must be updated by hand to match** — this site does not (yet) regenerate
itself from `out/results/*.json` the way `render_report.py` does. A future
ticket could template it from the same JSON pipeline; out of scope for the
first version.

## Viewing locally

```
python3 -m http.server 8000 --directory marketing-site
# open http://localhost:8000
```

No install, no `npm run dev` — just open `index.html` directly, or serve it
as a static file from anywhere (GitHub Pages, S3, Vercel static, etc).

## Files

- `index.html` — all copy/structure/sections.
- `styles.css` — full dark theme, one file, CSS custom properties for the palette, plus the interactivity/motion layer (scroll-progress, cursor-glow, blobs, tilt, stagger, button shimmer, the Auditor monitor, the force-directed graph).
- `script.js` — reveal-on-scroll/stagger, count-up/bar animation, scroll-progress, cursor-glow, tilt, active-nav, eyebrow cycler, Auditor monitor replay, draggable force-directed graph. No dependency.

## Naming note

This product is branded **ContextOS** on this site. `architecture.md` at the
repo root documents an earlier, unrelated, abandoned project concept that
also used the name "ContextOS" (a context-compaction runtime, archived at
`docs/archive/architecture-contextos.md`). That earlier concept has nothing
to do with this product; the name was intentionally reused per the current
product owner's direction. Flagging here only so nobody reading both docs
gets confused later.
