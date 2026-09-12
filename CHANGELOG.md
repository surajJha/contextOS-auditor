# Changelog

All notable changes to `contextos-auditor` are documented here.

## [0.2.1] — 2026-09-12

Corrects the gap between the published 0.2.0 artifact and the verified
source, plus defects found by testing fresh installs on native Windows,
macOS and Linux. Zero required runtime dependencies remain unchanged.

### Fixed

- Default session IDs include a UUID so runs created in the same millisecond
  cannot share capture files. Explicit caller-provided IDs are unchanged.
- CrewAI detachment drains queued callbacks before finishing the recording.
  Detached adapters cannot resume capturing later runs.
- AutoGen streaming records terminal usage before yielding it and closes the
  underlying stream when the wrapper is closed.
- OpenAI Agents handles cleanup warnings without a missing-name crash and
  ignores aggregate task/turn spans rather than counting their usage twice.
- Published accounting now includes source fixes for clamped per-row waste,
  zero-usage sessions, synthetic flush turns and duplicate carry horizons.
- `load_events` warns when unreadable records make totals incomplete.
- Windows console output escapes characters unsupported by its encoding;
  captured files and HTML retain UTF-8 text.
- Failed empty-history HTML writes return an error. Poll intervals must be
  positive and finite. Error messages link to the actual public issue tracker.
- Offline tokenizer loading checks the expected cache file and checksum,
  honors `TIKTOKEN_CACHE_DIR`, and does not treat unrelated cache files as
  permission to download. Reports distinguish local text estimates from
  provider-reported usage.
- Savings and privacy notices no longer promise exact recoverable dollars
  or deny explicitly enabled OpenTelemetry export.
- Package `__version__` matches distribution metadata. The CLI supports
  `--version` for installation diagnostics.

### Installation and compatibility

- Documented same-agent virtual environments, quoted extras, Windows,
  headless/SSH/container use, and the difference between hook dependencies
  and full framework runtimes. No standalone binary download is promised.
- Base package coverage includes Python 3.10–3.14 on Windows, macOS and Linux,
  plus Python 3.12 in Alpine. Framework SDKs have their own constraints:
  current CrewAI releases require Python below 3.14.
- Real-SDK scenarios cover sync, async and streaming paths using deterministic
  offline model responses, not paid live provider calls.
- CrewAI and OpenAI process-global hooks support one active audit run per
  process, not isolation between overlapping independent requests.

## [0.2.0] — 2026-09-06

**Artifact correction (2026-09-12):** some fixes described below existed in
source but were absent from the published 0.2.0 artifact. That artifact also
reported internal version 0.1.0. Version 0.2.1 aligns the distribution with
the corrected source and adds installed-artifact regression gates.

A dedicated bug-bash release, plus the dashboard work that came out of it.
Four adversarial audits (adapters, recording core, report rendering,
internals) and a performance audit produced 43 findings; every one was
reproduced before it was fixed. Test count went from 129 to 329 and branch
coverage from 85% to 89%.

Nothing here changes the *shape* of the data you already collect, and the
package still has **zero runtime dependencies**.

### Added

- **`history --html`** writes the run-over-run view as a self-contained
  page with sparklines for tokens, cost and estimated savings. `history`
  was stdout-only, so the one view that shows a trend *across* runs could
  not be shared or attached to a PR — and a fixed-width terminal table is
  the wrong medium for a trend anyway. No scripts, no network, no build
  step. Both renderers are driven from a single read pass, so the HTML
  cannot become a more flattering version of the terminal table: skipped
  sessions are named, a run with unreadable lines is flagged on its own
  row, and an unpriced model shows `n/a` instead of being plotted on the
  floor of the cost chart as a free run.

### Changed — one definition of the theme

- **Dashboard and marketing-site colours can no longer drift.** The two
  hand-copied the same ten hex values into two `:root` blocks in two
  languages, and nothing failed when one of them changed. The palette is
  now defined once in `_internal/theme.py`, the dashboard stylesheet is
  generated from it at import time (no per-render cost), and a parity test
  diffs it against `marketing-site/styles.css` so an unmirrored change
  fails the suite. The differing font stacks are asserted as intentional
  rather than ignored: the dashboard must render offline, so it never
  references a CDN font.

### Changed — the live dashboard is now usable *while* it is live

- **`watch --serve` no longer rebuilds the page on every poll.** The SSE
  client used to assign `document.body.innerHTML` once per poll interval,
  destroying and recreating every node in the document. The numbers were
  right but the page was hard to read: it flashed, the scroll position
  jumped back to the top, text selection was lost, and any `<details>`
  trace the user had opened to inspect a turn slammed shut roughly once a
  second. The client now walks the incoming fragment and patches only the
  subtrees that actually changed, so updating one token count touches one
  text node and leaves the rest of the document — including scroll
  position, focus and open disclosures — completely untouched.
- **A `<details>` you opened stays open.** Disclosure state belongs to the
  reader, so the `open` attribute is deliberately never synced from the
  server.
- **Idle runs no longer push redundant updates.** An agent that hasn't
  produced a new turn renders identically every poll; `/events` now sends
  an SSE comment heartbeat instead of re-sending a byte-identical payload.
  Measured on a 0.3 s poll: 1 payload + 13 heartbeats over 4 s, where
  previously all 14 frames carried a full re-render.
- If patching ever fails, the client falls back to the old full-body swap.
  A dashboard that flashes is a nuisance; one that silently freezes on
  stale numbers while the run continues is a correctness problem.

The patcher was verified by executing the shipped script in a real DOM
(node identity across updates, open `<details>`, retained focus, appended
and removed turns, malformed payloads); the previous client fails 15 of
those assertions.

### Fixed — numbers that were wrong

These are the serious ones: in each case the auditor printed a confident
number that was not true.

- **openai-agents recorded ZERO tokens on the SDK's default configuration**:
  the adapter only handled `generation` spans, but `OpenAIResponsesModel`
  (the default) emits `response` spans. Every session using the default
  model client reported no LLM turns at all.
- **Duplicate-read waste was silently zeroed** for any framework that
  reports only `total_tokens` (no prompt/completion split), because the
  clamp used `actual_prompt`, which was 0.
- **`save_pct` could exceed 100%** (observed: 4998%) — a savings estimate
  larger than the entire session's spend. Now clamped, and the itemised
  per-row waste is scaled to match the clamped total at source, so no two
  sections of one report can disagree.
- **`finish()` emitted a phantom zero-usage turn** that extended the
  duplicate-carry horizon by a turn nobody paid for, roughly doubling
  reported waste on short sessions. Such flush turns are now marked
  `synthetic` and excluded from billing math.
- **Duplicate reads were missed** when the same file arrived as `a.py`,
  `./a.py` and `.\a.py`. Paths are now normalised (case preserved).
- **The CTA multiplied a token-share percentage by a dollar total.** Waste
  is duplicated *prompt* context, and input tokens are priced far below
  output tokens, so this overstated the dollar saving on essentially every
  session. Waste is now priced at the model's input rate.
- **Duplicate detection never fired for object arguments**: the call
  signature was built with `repr()`, which embeds a memory address, so two
  structurally identical calls always looked different. Signatures are now
  structural and order-independent.
- **The pricing table missed nearly every real-world model string** —
  `gpt-4o-2024-08-06`, `claude-sonnet-4-5`,
  `us.anthropic.claude-...-v1:0`, `openrouter/...` — so real sessions
  showed no dollar figure at all. Prefixes, version suffixes and aliases
  now resolve. This only *renames*; no price point was invented.
- **Chart and trace rows disagreed**: the per-turn chart plotted
  prompt+completion while the rows printed `total_tokens`, so a
  totals-only framework got an empty chart beside real numbers.

### Fixed — crashes and data loss

- **A read-only or full disk crashed the user's agent.**
  `FrameworkAuditSession.__init__` was the one entry point not guarded, so
  `PermissionError` propagated straight out of `AuditedCrew(...)`.
  Auditing now degrades to a visibly disabled no-op session and warns once.
- **One torn JSONL line made an entire session unreadable** — which is the
  *normal* state of a file being appended to live. Good turns are now
  always readable, and the count of skipped lines is reported rather than
  hidden.
- **A whole turn was dropped** (and its tool calls re-attributed to the
  next turn) when a tool argument was not JSON-serialisable, when a usage
  value was non-integer, or when a tool name was not a string. The rule
  throughout is now: degrade to a visible zero, never to an invisible
  omission.
- **Failed tool calls were invisible**, and LangGraph leaked pending tool
  state on error. Errors are now recorded as the tool result.
- **`attach()` was not idempotent** — calling it twice doubled every
  number. `detach()` failures were silent and could record into a
  finished session.
- **AutoGen**: dict-shaped usage recorded as 0/0; a partially consumed
  `create_stream` lost the entire turn; async tools were mis-detected.
- **CrewAI adapter had never executed a single line in any test** (a
  module-level `importorskip` hid it) despite shipping publicly. It now
  has a full stub-based suite, including 8-thread concurrency.
- `doctor` crashed on an SDK that failed to import for any reason other
  than `ImportError`; `--limit` accepted negatives; `--port 99999` raised
  a raw `OverflowError`; `--html` was silently ignored with `--serve`.

### Fixed — security and privacy

- **Modern API keys were written to disk in plaintext.** The `sk-` pattern
  required 20+ alphanumerics immediately after the prefix, so every
  multi-segment key (`sk-proj-…`, `sk-ant-api03-…`) fell straight through.
  Added Stripe, Google and connection-string patterns; for connection
  strings and `key = value` assignments only the secret itself is
  replaced, so the surrounding context stays readable.
- **Redaction only scrubbed top-level string arguments** — nested dicts and
  lists passed through raw. It now recurses (depth-bounded, cycle-safe).
- **Catastrophic backtracking in the PEM pattern** (1.88s on a 128KB tool
  result, in the agent's hot path) is now bounded and linear.
- **The local dashboard answered any `Host` header**, so a DNS-rebinding
  page could read your tool arguments and file contents cross-origin.
  Non-loopback hosts now get 403 on every route.
- **`tiktoken` triggered a real network download.** Exact tokenizers are
  now opt-in (already-cached, or `CONTEXTOS_TIKTOKEN_DOWNLOAD=1`), and the
  report says out loud when counts are the chars/4 approximation.

### Performance

Measured over 2,000 turns with 3 tool calls each (see `bench_overhead.py`):

| | before | after |
|---|---|---|
| CPU per recorded turn | 1.65 ms | **0.40 ms** |
| p99 per turn | 2.14 ms | **0.62 ms** |
| CPU for 2,000 turns | 2.93 s | **0.68 s** |

**4.1x faster.** Cost per turn is flat regardless of session length (O(1),
verified to 4,000 turns), and retained memory stays under 0.3 MB.

- **Redaction was 55% of all auditor CPU.** It now runs a cheap literal
  pre-filter first and skips any pattern that provably cannot match.
  Because a wrong pre-filter would leak a secret, the fast path is proven
  byte-identical to the naive implementation on curated secrets and 3,000
  fuzzed inputs.
- **`session.json` was fully rewritten and atomically renamed on every
  turn** (28% of CPU) although its content is identical except a timestamp
  nothing reads. Throttled to once a second; terminal writes are never
  throttled, and `events.jsonl` is still appended synchronously.
- **OpenTelemetry export delayed interpreter exit by ~7s and leaked a
  thread per session.** Providers are now shared per endpoint with bounded
  timeouts: measured 12.33s → 1.33s exit, 5 threads → 1.
- Two unbounded memory leaks fixed (duplicate-tracking maps, and a
  100k-entry cache that retained full file bodies).

### Changed

- One-shot HTML exports (`report --html`, `demo --html`,
  `watch --serve --html`) no longer carry a 2-second meta-refresh and a
  footer promising updates that would never arrive. `watch --html`, which
  really does rewrite the file, still refreshes.
- Optional extras now declare version floors matching the verified compat
  ranges (`crewai>=0.100`, `openai-agents>=0.1`).
- An unknown turn number renders as `?` rather than the literal `None`.

### Known limitations

- Under CrewAI's threaded event bus (`ThreadPoolExecutor`), tool calls can
  be attributed to an adjacent turn. Per-call recording is atomic and
  nothing is lost or duplicated; only cross-call ordering is
  best-effort. This is covered by an explicit concurrency test rather than
  papered over.

## [0.1.1] — 2026-09-04

### Fixed
- **LangGraph adapter could silently drop a whole turn (LNCH-008)**:
  `AuditorCallback._extract_usage` caught only `AttributeError`/`IndexError`/
  `TypeError`, so a usage object that wasn't a mapping raised `ValueError`
  out of `dict(usage_metadata)`. `on_llm_end`'s guard then swallowed the
  entire call, so the turn was never emitted — which both shrank the turn
  count *and* re-attributed that turn's buffered tool calls to the next
  turn, corrupting the trace rather than merely shortening it. An
  unrecognised usage shape now degrades to zeros (a visible "no tokens
  seen") while the turn and its tool attribution stay correct.
- **`contextos-auditor demo` doubled its own numbers on a second run**: the
  demo writes to a fixed session id but never cleared it, so re-running
  appended to the previous session. Turns, waste and duplicate reads all
  doubled and the headline percentage was fabricated. The session is now
  reset before each run.
- **First-run notice printed a malformed path** (`.//tmp/x`) when the audit
  root sat outside the working directory.

### Changed
- The conversion CTA no longer prints `pip install contextos-optimiser`.
  The optimiser is in private release and that command does not resolve;
  it now points to email for access.

### Added
- 49 adapter-level tests covering LangGraph, OpenAI Agents SDK and AutoGen
  (previously only CrewAI had them), driven by import-time stubs so the
  suite still runs on a bare `pip install pytest` and the package keeps its
  zero-dependency guarantee.
- Animated dashboard demo in the README.

## [0.1.0] — 2026-09-04

First release published to PyPI: `pip install contextos-auditor`.

### Added
- **Nested turn → tool-call trace (AUD-013)**: `report`/`watch` (terminal
  and HTML/live dashboard) now render a per-turn breakdown of which tools
  were called and which write triggered waste detection, instead of only
  bottom-line totals -- built from data `shadow_session()` already
  computes, no new instrumentation. Live-verified via screenshot.
- **`contextos-auditor history` command (AUD-014)**: lists every recorded
  session under `--audit-root`, newest first, with framework/model/status/
  turns/tokens/cost/save% columns, `--limit N` (default 20, `0` = no cap).
  Reads only the session files each run already writes; no new database
  or aggregation step.
- **Optional secret-pattern redaction (AUD-016)**: `attach(...,
  redact_secrets=True)` (or `CONTEXTOS_REDACT_SECRETS=1`) scrubs
  recognizable secret-shaped substrings (AWS/OpenAI/GitHub/Slack-style
  keys, JWTs, PEM private keys, generic `api_key=`/`password=`/`token=`
  assignments) from recorded tool args/results with `[REDACTED]`,
  replacing only the matched value -- not the whole tool call, so real
  file content the write-waste detector needs stays intact. Off by
  default; live-verified that waste detection keeps working correctly
  with it turned on.
- **Optional OTel export (AUD-012)**: `pip install contextos-auditor[otel]`
  plus `attach(..., otel_endpoint="http://localhost:4318/v1/traces")` (or
  the `CONTEXTOS_OTEL_ENDPOINT` env var, zero code changes) makes every
  adapter additionally export each turn as a real OTel span following the
  GenAI semantic conventions (`gen_ai.system`, `gen_ai.request.model`,
  `gen_ai.usage.input_tokens/output_tokens`, `gen_ai.tool.name`, ...) --
  verified against a real local OTLP/HTTP receiver. Strictly additive and
  opt-in: the local JSONL trail is unaffected either way, and a missing
  package or unreachable collector degrades to one `UserWarning`, never a
  crash in the agent's real run.
- **Real $ cost estimate (AUD-011)**: sessions for models in a small,
  hand-curated, dated pricing snapshot (`_internal/pricing.py`, sourced
  from litellm's publicly maintained pricing table) now show an
  estimated `$` figure alongside token counts in `watch`/`watch --serve`/
  `report`, clearly labeled "list price as of DATE, not your negotiated
  rate". Unpriced models show "no dated pricing available" rather than a
  guessed number — the Auditor's honesty-culture rule (never invent a
  billing figure) still holds, this just widens what counts as "real".
- Fixed **AUD-018**: the model name is now backfilled from the first real
  LLM event a framework reports (e.g. CrewAI's
  `LLMCallCompletedEvent.model`) instead of staying `unknown` for the
  entire session when no `model_hint` was passed to `attach()`.
- Fixed **AUD-017/AUD-019**: `watch`/`watch --serve` now poll and wait for
  a session to appear instead of exiting immediately if the dashboard is
  opened before the agent run starts (at least as natural an order as the
  reverse). Plain `watch` also now detects a finished/errored session and
  stops on its own instead of refreshing forever until Ctrl-C.
- Free, local-only Agent Auditor (**free for any use, commercial included
  — licensed under PolyForm Shield 1.0.0, noncompete only, see LICENSE**):
  attach one line to an
  existing CrewAI / LangGraph / AutoGen / OpenAI Agents SDK agent and
  watch its real token cost live. No signup, no telemetry, no data leaves
  the machine it runs on.
- **Crash isolation (AUD-009)**: every adapter handler and
  `FrameworkAuditSession` entry point is now `@guarded` — an internal
  Auditor failure (malformed usage dict, SDK version drift, full disk)
  can never propagate into the agent's real execution path. Fixed a
  critical case in the AutoGen adapter where a recording failure could
  previously discard an already-successful, already-billed LLM call
  result.
- Public adapters for all 4 frameworks (`contextos_auditor.crewai`,
  `.langgraph`, `.openai_agents`, `.autogen`), each vendoring (not
  importing) the corresponding internal `dashboard/adapters/*` logic so
  the package has zero dependency on the toku monorepo at runtime.
- `contextos-auditor` CLI: `watch` (terminal table, auto-refreshing HTML,
  or `--serve` for a localhost-only live browser view over Server-Sent
  Events), `report` (one-shot static summary), `doctor` (checks which
  framework SDKs are importable and whether their installed version falls
  inside this release's tested range).
- Project-local `.contextos/audit/` output directory by default (no
  assumption the caller lives inside any particular repo layout).
- Per-framework `[crewai]`/`[langgraph]`/`[autogen]`/`[openai-agents]`/
  `[all]` optional extras; zero required runtime dependencies.
- Hard, CI-enforced network-isolation test suite
  (`tests/test_no_network_calls.py`): the live `--serve` view is proven,
  not just claimed, to make outbound connections only to loopback.
- SDK version-drift detection (`_internal/compat.py`): a one-time
  `UserWarning` (never a hard failure) when an installed framework SDK
  version falls outside the range the vendored adapter was last verified
  against, surfaced proactively in `contextos-auditor doctor`.
- `scripts/build_binary.sh`: builds a standalone, single-file CLI binary
  (PyInstaller, built from a clean venv with zero framework extras) for
  teammates who want to `watch`/`report`/`doctor` a session without any
  Python/pip on their machine at all. Verified to run correctly in a fully
  empty environment (`env -i`, no PATH/PYTHONHOME).

### Fixed
- **P0**: `watch --serve`'s live dashboard corrupted itself into visible
  raw `\n`/quote-mark text after the very first live update. The server
  correctly JSON-encodes each SSE fragment (`json.dumps(fragment)`) but
  the injected client-side JS assigned the raw, still-encoded string
  straight into `document.body.innerHTML` instead of decoding it first.
  Found via a real headless-Chrome screenshot of the running dashboard,
  not code review. Fixed: client now does `JSON.parse(e.data)` before
  use. Regression test: `tests/test_server_sse_parse.py`.
- Restyled `watch`/`watch --serve`/`--html`'s dashboard to match the
  marketing site's dark theme (same background/panel/accent colors,
  mono-font numeric table, teal-for-positive/red-for-negative percent)
  instead of the previous unstyled light-mode default — this is now the
  one visual identity used everywhere the product shows a UI. Deliberately
  does not load the marketing site's Google Fonts (system-font fallback
  only), keeping the dashboard's own zero-external-network guarantee
  (AUD-005) intact for the actual tool, not just the marketing page.

### Known limitations
- The `_internal/*.py` vendor copies are manually synced against the
  monorepo's `dashboard/`/`kit/`/`spike/` originals — not automatic. See
  README.
- No hosted/team dashboard in this package by design; that's a paid-tier
  feature. This package is local-only, always.
