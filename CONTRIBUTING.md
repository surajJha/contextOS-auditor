# Contributing

## Before anything else

This project is licensed **PolyForm Shield 1.0.0**. It is free for any use,
including commercial use inside your own company. It does not allow you to
build a competing product out of it. By sending a patch you agree your
contribution ships under that same licence.

## Setting up

The base package has zero required runtime dependencies. Install it and
the `pytest` development extra in a virtual environment:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

The unit suite uses framework stand-ins, so real SDKs are not required.
For optional real-SDK checks, install the selected framework and run
`python -m pytest scripts/test_sdk_integration_smoke.py`. These checks use
real SDK orchestration with deterministic offline model responses, not
paid provider calls.

## The one rule that matters

**Every number this tool prints has to be defensible.**

If you change how a figure is calculated, a test must pin the new value, and
the README's "What it actually measures" section must still describe what the
code actually does. A number nobody can reconstruct from the trace is worse
than no number at all — the entire value of this project is that its output
holds up when someone checks it against their bill.

Related: reported savings are **estimates, not guaranteed bill reductions**.
Do not remove the
caveats, the `ESTIMATED` badges, or the clamp that stops reported waste from
exceeding tokens actually spent.

## Things that will get a patch rejected

- Adding a runtime dependency. `pip install contextos-auditor` must continue to
  install nothing but itself. Framework SDKs belong in `[project.optional-dependencies]`.
- Sending data off the machine by default.
- Making the auditor able to break the user's agent. Every hook is wrapped so
  that an internal error degrades to a dropped observation and a warning — the
  real run must never fail because the auditor did. See `tests/test_crash_isolation.py`.
- Turning off secret redaction by default.

## Style

No linter is enforced. Match the surrounding code. Comments should explain why
something is done, not restate what the line does.

## Reporting bugs

Use the issue templates. If `contextos-auditor demo` reproduces the problem,
say so — it needs no agent, no API key and no network, which makes it by far
the fastest thing to debug.

On 0.3.0+, `contextos-auditor troubleshoot` provides symptom-based help.
For an issue, include your version, framework, minimal reproduction, and
a reviewed `doctor --json` report. Do not share raw traces or credentials.
