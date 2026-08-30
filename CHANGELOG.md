# Changelog

All notable changes to `contextos-auditor` are documented here.

## [0.1.0] — unreleased (pre-PyPI)

Initial public release candidate. Not yet published to PyPI — install from
source in the meantime (see README Quickstart).

### Added
- Free, local-only Agent Auditor: attach one line to an existing
  CrewAI / LangGraph / AutoGen / OpenAI Agents SDK agent and watch its
  real token cost live. No signup, no telemetry, no data leaves the
  machine it runs on.
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

### Known limitations
- The `_internal/*.py` vendor copies are manually synced against the
  monorepo's `dashboard/`/`kit/`/`spike/` originals — not automatic. See
  README.
- No hosted/team dashboard in this package by design; that's a paid-tier
  feature. This package is local-only, always.
