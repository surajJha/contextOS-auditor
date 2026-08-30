# Changelog

All notable changes to `contextos-auditor` are documented here.

## [0.1.0] — unreleased (pre-PyPI)

Initial public release candidate. Not yet published to PyPI — install from
source in the meantime (see README Quickstart).

### Added
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
- Free, local-only Agent Auditor (**noncommercial use only** — licensed
  under PolyForm Noncommercial 1.0.0, see LICENSE): attach one line to an
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
