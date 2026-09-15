# Security and privacy

## The short version

`contextos-auditor` records locally by default and requires no account.
Recordings normally live under `.contextos/audit/` in your working directory;
adapters can use a custom output directory. The optional dashboard server
binds to loopback by default. Do not expose it publicly or share raw recordings.

Recording export is opt-in and off unless you configure it: the OpenTelemetry
exporter (`CONTEXTOS_OTEL_ENDPOINT`), which sends spans to a collector *you*
choose.

Your agent and its SDKs have their own network behavior. Auditor does not
replace their privacy settings. A synthetic demo or recorder self-test
does not make paid model calls.

## What gets written to disk

Tool arguments and results are recorded so the auditor can tell that content
was re-sent to the model. That means **your source code and prompts can end up
in `.contextos/`**. Treat that directory like your source tree:

- Exclude `.contextos/` and any custom recording directory from version control.
- Secret-shaped values (API keys, tokens, passwords, connection strings) are
  redacted by default before anything is written. Set
  `CONTEXTOS_REDACT_SECRETS=0` to disable this, but there is rarely a reason to.
- Redaction is pattern-based. It is a safety net, not a guarantee. Do not paste
  raw session files into public issues.

For support, use `contextos-auditor doctor --check-recording --json` on
0.3.0 or later. Its allowlisted report excludes paths, prompts, session data,
environment values, and raw exceptions. Review it before sharing. Plain
console details and debug tracebacks are not sanitized support reports.

## Reporting a vulnerability

Email **skj48817@gmail.com**. Please do not open a public issue for anything
that could expose another user's data.

Include what you did, what happened, and why you think it is a security problem.
A proof of concept helps but is not required.

Expect an acknowledgement within a few days. This is a small project, not a
company with an on-call rotation — but data-exposure reports are taken
seriously and prioritised over features.

## Scope

In scope:

- Anything that causes data to leave the machine without the user asking.
- Anything that writes secrets to disk unredacted through a normal code path.
- Anything that lets a malicious agent trace escalate into code execution.

Out of scope:

- The fact that recorded tool output contains your own source code. That is the
  documented design; use `.gitignore` and do not share the directory.
- Redaction failing to catch a novel secret format. Report it as a normal issue
  so the pattern can be added.
