# Security and privacy

## The short version

`contextos-auditor` runs entirely on your machine. It opens no sockets, sends
no telemetry, and requires no account. Everything it records is written under
`.contextos/` in your working directory, and deleting that directory deletes
all of it.

The one exception is opt-in and off unless you configure it: the OpenTelemetry
exporter (`CONTEXTOS_OTEL_ENDPOINT`), which sends spans to a collector *you*
choose.

## What gets written to disk

Tool arguments and results are recorded so the auditor can tell that content
was re-sent to the model. That means **your source code and prompts can end up
in `.contextos/`**. Treat that directory like your source tree:

- It is added to `.gitignore` on first run.
- Secret-shaped values (API keys, tokens, passwords, connection strings) are
  redacted by default before anything is written. Set
  `CONTEXTOS_REDACT_SECRETS=0` to disable this, but there is rarely a reason to.
- Redaction is pattern-based. It is a safety net, not a guarantee. Do not paste
  raw session files into public issues.

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
