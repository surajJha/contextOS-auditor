# ContextOS marketing site

Static HTML, CSS, and JavaScript. No build, package installation, CDN fonts,
analytics, visualization library, or external runtime request.

## Local preview

```bash
python3 -m http.server 8847 --bind 127.0.0.1 --directory marketing-site
```

Open <http://127.0.0.1:8847/>. All product explanations and the benchmark
matrix remain readable without JavaScript.

## Design and interactions

- **Agent lifecycle:** a looping 96-second SVG engine tour with seven exploded
  logical layers, moving request paths, rotating mechanisms, and a separate
  Auditor observation rail. Eight chapters cover attachment, context assembly,
  inference, reads/searches, edits, guards, feedback, and inspection. Playback,
  scrubbing, chapter jumps, 0.5–2x speed, assembled/exploded views, and baseline
  comparison are keyboard-operable. Comparisons keep the Auditor attached.
  The complete narration and framework-specific attachment details remain
  available in an HTML transcript without JavaScript.
- **Context observatory:** CSS 3D layers and perspective-projected Canvas
  particles show baseline, Auditor, and Optimiser states. The scene is an
  illustration, not a live run or a numerical savings forecast. Only supported
  tool surfaces change in the optimised scene; instructions and conversation
  history are not depicted as rewritten.
- **Mechanism explorer:** five selectable, schematic before/after payloads
  explain focused edits, selective reads, bounded searches, task-scoped tool
  menus, and eligible redundant-call avoidance. Each includes its limitation.
  Tile counts are decorative, not token measurements or fixed saving ratios.
- **Replay explainer:** a keyboard-accessible slider visualizes the
  triangular accumulation of equal-sized context chunks across turns.
  It explicitly excludes token pricing, caching, and output costs.
- **Evidence explorer:** framework buttons update seven measured-domain
  bars. Values and uncertainty markers come from the visible HTML matrix,
  not a second JavaScript data copy. A 0–100% axis and adjacent uncertainty
  legend make the scale and caveats readable without opening the matrix.
- **Progressive detail:** product internals, the full matrix, methodology,
  limitations, and demo setup use native `details` elements.
- **Quickstart:** keyboard-operable framework tabs and clipboard buttons
  with an honest manual-copy fallback.

Canvas work stops when the scene is offscreen or the page is hidden. Pixel
density is capped at 2 and drawing at roughly 30 fps. A motion control and
the OS reduced-motion setting disable decorative movement. Content is
never hidden behind scroll-triggered reveal scripts.

The lifecycle tour uses its own visible-only, approximately 30 fps scheduler.
It pauses work offscreen or in a hidden tab without jumping ahead on return.
Reduced motion starts the tour paused and disables gear/packet movement and
explosion tweening; explicit playback still advances the explanatory chapters.
Selecting a chapter, scrubbing, or comparing configurations pauses playback.
Automatic narration does not generate screen-reader live announcements;
user-initiated controls announce their changes. On narrow screens the diagram
scrolls within its own region instead of shrinking its labels beyond legibility.

System-font stacks and palette match the local Auditor dashboard. The
existing theme-parity tests enforce this relationship.
Main body copy uses 16–18px type, with a 21–24px hero description, stronger
heading weights, and larger controls and notes. Mobile layouts adapt rather
than shrinking important copy back to tiny labels.

## Measurement boundaries

Historical numbers match `docs/product/live-results-2026-08-30.md`.
The headline and matrix describe the dated pre-consolidation benchmark,
not current-source revalidation or a guaranteed customer outcome.

The lifecycle is a conceptual walkthrough, not a recording, live telemetry,
model-internals visualization, or measured timeline. Optimisation is confined
to supported tool surfaces. The Auditor is represented as in-process
instrumentation, not an execution proxy: it captures exposed model/tool events,
redacts captured text, persists local observations, and supports shadow
estimates. Redaction is not comprehensive; estimates are not observed savings.

Cost weighting uses prompt + 8 times completion tokens for gpt-5-mini.
It is a list-price reference, not an invoice or Copilot-seat bill. `‡`
preserves raw-token uncertainty; the CrewAI e-commerce regression remains
visible. Auditor shadow estimates are explicitly separate from measured
provider usage.

When evidence changes, update the matrix and framework totals together.
`marketing-site/tests/test_content.py` checks them against the source
document, so drift fails rather than silently reaching a visual.

## Files

`index.html` holds content and the benchmark matrix; `styles.css` holds the
responsive layout and 3D scene; `script.js` holds interactions. Existing
social images and icons live in `assets/`.
`lifecycle.css` and `lifecycle.js` isolate the guided engine tour; its narration,
route metadata, and SVG geometry live in `index.html`. Chapter duration is
configured on `#lifecycle`; controls derive the full duration from its transcript.

## Deployment status

The live site is <https://contextos-ai.pages.dev/> (verified HTTP 200 on
2026-09-12). Canonical, Open Graph, and Twitter image URLs use that origin.
The unrelated `contextos.ai` domain is not owned by this project and must
not be used in deployment metadata. Local changes do not update the live
site until a deployment occurs.

The existing manual `.github/workflows/azure-static-web-apps.yml` uploads
only `marketing-site/`. To use it later, configure an existing app's
`AZURE_STATIC_WEB_APPS_API_TOKEN` repository secret, dispatch from `main`,
and verify the resulting URL and domain metadata before announcing it.

The Auditor's public repository is
<https://github.com/surajJha/contextOS-auditor>; its README and the PyPI
package description need a separate source/release sync. There were no
published binary releases when checked on 2026-09-12. Do not advertise a
standalone download until platform assets actually exist.

## Social card

`assets/og-card.html` is the source for `assets/og.png` (1200 × 630).
Keep it independent of unqualified benchmark percentages: reported usage,
list-price estimates, and potential savings are different claims.
After changing the template, render it with an available headless browser,
check the image, and deploy the HTML and PNG together.
