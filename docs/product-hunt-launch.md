# Product Hunt launch copy — contextos-auditor

Working draft. Numbers below are real, taken from a live demo run (not
invented) — re-verify against a fresh `watch --once` before publishing if
more than a few weeks have passed, since pricing/behavior can drift.

## Tagline (60 chars max)

**"See what your agent actually costs. No signup, no cloud, ever."**

Alt (more literal about the free/local angle):
> A free, local-only cost & waste auditor for your CrewAI/LangGraph/AutoGen agent — zero setup, zero data leaves your machine.

## First comment / maker post

> Hey PH! 👋
>
> I kept losing track of what my agents were actually costing me —
> not "roughly", the real per-run token/dollar number — and I didn't
> want to create an account or ship my prompts/tool outputs to a SaaS
> just to find out.
>
> **contextos-auditor** is a free, local-only auditor you attach to an
> existing CrewAI / LangGraph / AutoGen / OpenAI Agents SDK agent with
> one line of code. It watches real LLM calls and tool calls as they
> happen and shows you, live, in your terminal or a local dashboard:
>
> - actual prompt/completion/total tokens per turn
> - an estimated $ cost, computed from a dated, sourced pricing table
>   (never a guess — unpriced models show "n/a", not a fake number)
> - **waste detection**: if your agent reads a file then rewrites the
>   whole thing to change one line, it flags the wasted tokens and
>   estimates the savings if you'd only sent the diff
>
> It's not trying to be LangSmith or Langfuse — no traces-as-a-service,
> no team dashboards, no billing integration. It's the thing you run in
> 2 minutes, locally, when you just want an honest number before you
> decide whether a "real" observability platform is worth the setup.
>
> Would love feedback, especially from anyone running CrewAI/LangGraph
> agents in production who's been burned by a surprise token bill.

## Why not just use Langfuse / LangSmith / AgentOps? (FAQ answer)

> Those are great tools and if you need team-wide tracing, prompt
> versioning, evals, and a hosted dashboard, use them — this isn't a
> replacement for that.
>
> contextos-auditor solves one narrower problem: *"I want to know,
> right now, on my own machine, with zero signup and zero data leaving
> my laptop, what this specific agent run actually cost and whether it
> wasted tokens."* If you already run a full observability stack, you
> probably don't need this. If you don't — and most solo/indie agent
> builders don't have one wired up — this is a 2-minute way to get a
> real number before deciding whether to invest in one.

## Real screenshot (not a mockup)

`docs/screenshots/live-dashboard.png` — captured via headless Chrome
against a live `watch --serve` session running a real 3-agent CrewAI
crew (support triage → reply writer → config-file maintainer). Shows:

- 6 turns, 3,490 real tokens, $0.0030 estimated cost (gpt-5-mini,
  litellm pricing snapshot dated 2026-08-30)
- `waste_tokens=29` / `+0.8%` estimated savings — from the config
  agent's real full-file rewrite of a one-line change, the honest
  trigger for the tool's core value proposition

## Known, disclosed limitations (say these proactively, don't wait to be asked)

- Install today is `pip install git+https://github.com/...` — **not
  yet on PyPI**. This is real friction for a PH launch day; either
  publish to PyPI before launching, or say so plainly in the post so
  it isn't a surprise mid-thread.
- $ cost is list price from a dated public snapshot, not your
  negotiated enterprise rate.
- Waste/savings numbers are token-count-based estimates, not a
  guarantee — the tool never claims to change your bill.
- 4 frameworks supported today (CrewAI, LangGraph, AutoGen, OpenAI
  Agents SDK) — no framework support beyond that at launch.

## Decision (resolved)

Launching with the `pip install git+https://github.com/...` command as-is.
PyPI publish is a fast-follow, not a launch blocker. Say so plainly in
the first comment / FAQ if asked, rather than waiting to be called out.
