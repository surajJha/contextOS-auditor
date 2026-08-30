# contextos-auditor

The free, local-only Agent Auditor. Attach one line to an existing
CrewAI / LangGraph / AutoGen / OpenAI Agents SDK agent and watch its real
token cost live — no code changes to your tools/prompts, no signup, no
telemetry, **no data ever leaves your machine**.

```bash
pip install contextos-auditor[crewai]        # or [langgraph] / [autogen] / [openai-agents] / [all]
```

Requires Python >= 3.10. Zero required runtime dependencies — every
framework SDK above is an optional extra you opt into.

### "I don't have a Python env set up"

If you're attaching this to your own agent: CrewAI/LangGraph/AutoGen/
OpenAI Agents SDK are all Python-native frameworks, so if your agent runs
at all, you already have a working Python + pip — that's how you installed
the framework itself. The `attach()`/callback-handler snippet has to be
`pip install`ed into that *same* interpreter no matter what, since it hooks
the framework's own event bus from inside your process.

If your `pip install` fails with an `externally-managed-environment` error
(common on recent macOS/Debian system Pythons), use one of:

```bash
pipx install "contextos-auditor[crewai]"     # isolates it into its own venv automatically
uv pip install "contextos-auditor[crewai]"   # if you already use uv
```

If you just want to **view** a session someone else's agent produced (a
teammate, a CI run, a reviewer) and don't want to touch Python/pip at all,
grab the standalone `contextos-auditor` binary from the
[GitHub Releases](../../releases) page instead — it only does `watch`/
`report`/`doctor` (no framework adapter, since those must live inside the
agent's own process either way):

```bash
./contextos-auditor watch --serve   # same CLI, zero Python required
```

See `scripts/build_binary.sh` if you want to build one yourself.

## Quickstart

```python
# crewai
from contextos_auditor.crewai import attach
audit = attach(task="fix the bug")
crew.kickoff()
audit.detach()
```

```python
# langgraph / any langchain_core-based agent
from contextos_auditor.langgraph import AuditorCallback
handler = AuditorCallback(task="fix the bug")
graph.invoke({"messages": [...]}, config={"callbacks": [handler]})
handler.finish()
```

```python
# openai agents sdk
from contextos_auditor.openai_agents import attach
audit = attach(task="fix the bug")
result = await Runner.run(agent, "do the thing")
audit.detach()
```

```python
# autogen
from contextos_auditor.autogen import new_session, wrap_client, audit_tool
session = new_session(task="fix the bug")
client = wrap_client(real_client, session)
agent = AssistantAgent("coder", model_client=client, tools=[audit_tool(write_file, session)])
...
session.finish()
```

Then, from a terminal, while (or after) your agent runs:

```bash
contextos-auditor watch              # live terminal table, auto-picks the
                                       # most recent session
contextos-auditor watch --serve       # localhost-only live view in a browser
                                       # tab (Server-Sent Events, no polling)
contextos-auditor report <id> --html out.html   # one-shot static HTML report
contextos-auditor doctor              # check which framework hooks are
                                       # available in this environment
```

Not sure any of this is working? Run `contextos-auditor doctor` first —
it tells you exactly which framework SDKs it can see and prints the
correct snippet for each, before you touch your agent code at all.

## What it actually measures

Every number is a real token count pulled from the framework's own usage
reporting (CrewAI's `LLMCallCompletedEvent.usage`, LangChain's
`AIMessage.usage_metadata`, the OpenAI Agents SDK's tracing spans,
AutoGen's `ChatCompletionClient.create()` return value) — never a
heuristic or an extra network call. The "savings if Kit were attached"
figure is a **local, offline estimate** computed from your own recorded
tool calls (specifically: how much smaller a hunk-based edit would have
been than the full-file write your agent actually sent) — it does not
change your bill, and it is clearly labeled `estimated` everywhere it
appears, never presented as a measured result.

## Privacy

Session data (`events.jsonl`/`session.json`) is written to a directory on
your own disk (`./.contextos/audit/<session-id>/` by default) and never
transmitted anywhere by this package. `contextos-auditor watch --serve`
binds a small local HTTP server to `127.0.0.1` only — it is never
reachable from outside your machine, and the process makes zero outbound
network calls (see `tests/test_no_network_calls.py`).

## Where this comes from

This package is the public, standalone distribution of the same adapter
logic used inside the [toku](https://github.com/surajJha/toku) monorepo's
`dashboard/adapters/` (which is what the framework-comparison numbers on
the ContextOS marketing site are measured with) — copied here rather than
imported, so this package installs and runs with no dependency on that
monorepo being present. If the monorepo's adapters change, this package's
copies are updated to match by hand; they are not auto-synced.

## Development

```bash
pip install -e ".[dev,all]"
pytest tests/
```

## License

MIT
