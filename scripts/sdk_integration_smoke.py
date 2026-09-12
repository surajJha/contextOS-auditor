"""Offline integration checks using real SDK orchestration and deterministic models.

Run from the repository root (no API keys or network required):
    PYTHONPATH=packages/contextos-auditor-py .venv-fw/bin/python \
        packages/contextos-auditor-py/scripts/sdk_integration_smoke.py

Use --framework NAME to select a framework, --out-dir for retained audit evidence.
Use PYTHONPATH pointing at an extracted wheel to test published code instead.
SDK dependencies are deliberately not installed or modified by this script.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import ipaddress
import json
import os
from pathlib import Path
import socket
import threading
import traceback
import warnings
from unittest.mock import patch


def verify(session, *, turns=2, tools=1, prompt=20, completion=6):
    from contextos_auditor._internal.audit_emit import load_events

    events = load_events(session._session.dir)
    assert len(events) == turns, events
    calls = [call for event in events for call in event["tool_calls"]]
    assert len(calls) == tools, calls
    assert sum(e["usage"]["prompt_tokens"] for e in events) == prompt, events
    assert sum(e["usage"]["completion_tokens"] for e in events) == completion, events
    if tools:
        assert calls[0]["name"] == "lookup", calls
        assert calls[0]["args"] == {"key": "alpha"}, calls
        assert calls[0]["result_text"] == "value:alpha", calls
    return {"turns": turns, "tools": tools, "prompt_tokens": prompt,
            "completion_tokens": completion}


def crewai_smoke(root):
    from crewai import Agent, BaseLLM, Crew, Task
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.llm_events import LLMCallType
    from crewai.llms.base_llm import llm_call_context
    from crewai.tools import tool
    from pydantic import PrivateAttr
    from contextos_auditor.crewai import attach

    class OfflineLLM(BaseLLM):
        _calls: int = PrivateAttr(default=0)

        def call(self, messages, tools=None, callbacks=None,
                 available_functions=None, from_task=None, from_agent=None, **kwargs):
            self._calls += 1
            output = (
                'Thought: Look up alpha.\nAction: lookup\nAction Input: {"key":"alpha"}'
                if self._calls == 1 else "Thought: Done.\nFinal Answer: value:alpha"
            )
            usage = {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}
            self._track_token_usage_internal(usage)
            with llm_call_context():
                self._emit_call_started_event(messages, from_task=from_task, from_agent=from_agent)
                if self._effective_stream():
                    self._emit_stream_chunk_event(output, from_task=from_task, from_agent=from_agent)
                self._emit_call_completed_event(
                    output, LLMCallType.LLM_CALL, from_task=from_task,
                    from_agent=from_agent, messages=messages, usage=usage,
                )
            return output

        async def acall(self, *args, **kwargs):
            return self.call(*args, **kwargs)

        def supports_function_calling(self):
            return False

    @tool("lookup")
    def lookup(key: str) -> str:
        """Look up a deterministic value."""
        return f"value:{key}"

    results = {}
    for mode in ("sync", "async", "native_async", "stream", "astream"):
        llm = OfflineLLM(model="gpt-4o-mini")
        agent = Agent(role="Lookup", goal="Look up alpha", backstory="Lookup tester",
                      llm=llm, tools=[lookup], allow_delegation=False, max_iter=3)
        task = Task(description="Call lookup with alpha then return its value.",
                    expected_output="value:alpha", agent=agent)
        crew = Crew(agents=[agent], tasks=[task], cache=False, verbose=False,
                    tracing=False, stream=mode in ("stream", "astream"))
        audit = attach(task=f"offline crew {mode}", out_dir=root / mode)
        audit.attach()  # duplicate attachment must not duplicate accounting

        async def run_async(mode=mode, crew=crew):
            if mode == "async":
                return await crew.kickoff_async()
            result = await crew.akickoff()
            if mode == "astream":
                chunks = [item async for item in result]
                assert chunks, "CrewAI emitted no stream chunks"
                return result.result
            return result

        try:
            if mode in ("async", "native_async", "astream"):
                result = asyncio.run(run_async())
            else:
                result = crew.kickoff()
                if mode == "stream":
                    assert list(result), "CrewAI emitted no stream chunks"
                    result = result.result
            assert "value:alpha" in result.raw, result
        finally:
            audit.detach(success=True)
        results[mode] = verify(audit.session)
        before = results[mode]
        crew.stream = False
        crew.kickoff()  # detached callbacks must stay detached
        crewai_event_bus.flush()
        assert verify(audit.session) == before
        assert not audit._handlers
        for handlers in crewai_event_bus._sync_handlers.values():
            assert audit.on_llm_completed not in handlers
            assert audit.on_tool_finished not in handlers

    audit = attach(task="drain queued CrewAI callbacks", out_dir=root / "queued_detach")
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    original_record = audit.session.record_llm

    def delayed_record(*args, **kwargs):
        entered.set()
        assert release.wait(5), "release of queued callback timed out"
        original_record(*args, **kwargs)
        completed.set()

    audit.session.record_llm = delayed_record
    timer = threading.Timer(0.1, release.set)
    try:
        OfflineLLM(model="gpt-4o-mini").call("queued")
        assert entered.wait(5), "SDK failed to dispatch callback"
        timer.start()
        audit.detach(success=True)
        assert completed.is_set(), "detach returned before the queued callback finished"
        results["queued_detach"] = verify(audit.session, turns=1, tools=0, prompt=10, completion=3)
    finally:
        release.set()
        timer.cancel()
        crewai_event_bus.flush()
        audit.session.record_llm = original_record
        audit.detach()
    return results


def langgraph_smoke(root):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from langchain_core.tools import tool
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode, tools_condition
    from contextos_auditor.langgraph import AuditorCallback

    @tool
    def lookup(key: str) -> str:
        """Look up a deterministic value."""
        return f"value:{key}"

    results = {}
    for mode in ("sync", "async", "stream", "astream"):
        messages = [
            AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"key": "alpha"},
                                               "id": "call_lookup", "type": "tool_call"}]),
            AIMessage(content="value:alpha"),
        ]
        for message in messages:
            message.usage_metadata = {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}
            message.response_metadata = {"model_name": "gpt-4o-mini"}
        model = FakeMessagesListChatModel(responses=messages)

        def step(state, model=model):
            return {"messages": [model.invoke(state["messages"])]}

        graph = StateGraph(MessagesState)
        graph.add_node("agent", step)
        graph.add_node("tools", ToolNode([lookup]))
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
        graph.add_edge("tools", "agent")
        app = graph.compile()
        handler = AuditorCallback(task=f"offline graph {mode}", out_dir=root / mode)
        config = {"callbacks": [handler]}
        inputs = {"messages": [HumanMessage(content="lookup alpha")]}

        async def run_async(mode=mode, app=app, inputs=inputs, config=config):
            if mode == "astream":
                return [item async for item in app.astream(inputs, config, stream_mode="values")][-1]
            return await app.ainvoke(inputs, config)

        try:
            if mode in ("async", "astream"):
                result = asyncio.run(run_async())
            elif mode == "stream":
                result = list(app.stream(inputs, config, stream_mode="values"))[-1]
            else:
                result = app.invoke(inputs, config)
            assert result["messages"][-1].content == "value:alpha", result
        finally:
            handler.finish(success=True)
        assert not handler._pending_tool_starts
        results[mode] = verify(handler.session)
        app.invoke(inputs)  # callbacks are invocation-scoped, never monkeypatched
        assert verify(handler.session) == results[mode]

    class StreamingModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "offline-stream"

        def _generate(self, *args, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="value:alpha"))])

        def _stream(self, *args, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content="value:"))
            yield ChatGenerationChunk(message=AIMessageChunk(
                content="alpha", usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
                response_metadata={"model_name": "gpt-4o-mini"},
            ))

    for mode in ("llm_stream", "llm_astream"):
        handler = AuditorCallback(task=f"offline chunks {mode}", out_dir=root / mode)
        config = {"callbacks": [handler]}
        model = StreamingModel()

        async def stream_async(model=model, config=config):
            assert await lookup.ainvoke({"key": "alpha"}, config) == "value:alpha"
            return [chunk async for chunk in model.astream("lookup alpha", config)]

        try:
            if mode == "llm_astream":
                chunks = asyncio.run(stream_async())
            else:
                assert lookup.invoke({"key": "alpha"}, config) == "value:alpha"
                chunks = list(model.stream("lookup alpha", config))
            assert "".join(chunk.content for chunk in chunks) == "value:alpha"
        finally:
            handler.finish(success=True)
        results[mode] = verify(handler.session, turns=1, prompt=10, completion=3)
    return results


def openai_agents_smoke(root):
    import httpx
    from openai import AsyncOpenAI
    from agents import Agent, Runner, RunConfig, function_tool
    from agents.models.openai_responses import OpenAIResponsesModel
    from agents.tracing import set_trace_processors
    from agents.tracing.setup import get_trace_provider
    from contextos_auditor.openai_agents import attach

    @function_tool
    def lookup(key: str) -> str:
        """Look up a deterministic value."""
        return f"value:{key}"

    provider = get_trace_provider()
    original = provider._multi_processor._processors
    set_trace_processors([])  # no default exporter: local tracing remains enabled
    results = {}

    async def run(mode):
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            has_result = any(i.get("type") == "function_call_output"
                             for i in body["input"] if isinstance(i, dict))
            output = (
                [{"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                  "content": [{"type": "output_text", "text": "value:alpha", "annotations": []}]}]
                if has_result else
                [{"id": "fc_1", "call_id": "call_lookup", "type": "function_call",
                  "name": "lookup", "arguments": '{"key":"alpha"}', "status": "completed"}]
            )
            response = {"id": f"resp_{len(requests)}", "object": "response", "created_at": 0,
                        "status": "completed", "model": "gpt-4o-mini", "output": output,
                        "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
                        "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13,
                                  "input_tokens_details": {"cached_tokens": 0},
                                  "output_tokens_details": {"reasoning_tokens": 0}}}
            if body.get("stream"):
                events = [
                    {"type": "response.created", "sequence_number": 0,
                     "response": {**response, "status": "in_progress", "output": []}},
                    {"type": "response.output_item.added", "sequence_number": 1,
                     "output_index": 0, "item": output[0]},
                    {"type": "response.output_item.done", "sequence_number": 2,
                     "output_index": 0, "item": output[0]},
                    {"type": "response.completed", "sequence_number": 3, "response": response},
                ]
                sse = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)
            return httpx.Response(200, json=response)

        http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        client = AsyncOpenAI(api_key="offline-not-a-secret", http_client=http,
                             base_url="https://offline.invalid/v1", max_retries=0)
        agent = Agent(name="offline", model=OpenAIResponsesModel("gpt-4o-mini", client),
                      tools=[lookup])
        audit = attach(task=f"offline agents {mode}", out_dir=root / mode)
        audit.attach()
        try:
            if mode == "stream":
                result = Runner.run_streamed(agent, "lookup alpha",
                                             run_config=RunConfig(tracing_disabled=False))
                async for _ in result.stream_events():
                    pass
            else:
                result = await Runner.run(agent, "lookup alpha",
                                          run_config=RunConfig(tracing_disabled=False))
            assert result.final_output == "value:alpha", result
            assert len(requests) == 2, requests
        finally:
            audit.detach(success=True)
            await client.close()
        assert provider._multi_processor._processors == ()
        results[mode] = verify(audit.session)

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", UserWarning)
            for mode in ("async", "stream"):
                asyncio.run(run(mode))
        audit_warnings = [str(w.message) for w in caught if "contextos-auditor:" in str(w.message)]
        assert not audit_warnings, audit_warnings
    finally:
        set_trace_processors(list(original))
    assert provider._multi_processor._processors == original
    return results


def autogen_smoke(root):
    from autogen_agentchat.agents import AssistantAgent
    from autogen_core import FunctionCall
    from autogen_core.models import ChatCompletionClient, CreateResult, RequestUsage
    from contextos_auditor.autogen import audit_tool, new_session, wrap_client

    class OfflineClient(ChatCompletionClient):
        def __init__(self):
            self.calls = 0
            self.closed = False
            self.streams_closed = 0

        async def create(self, *args, **kwargs):
            self.calls += 1
            return CreateResult(
                finish_reason="function_calls" if self.calls == 1 else "stop",
                content=[FunctionCall(id="call_lookup", name="lookup", arguments='{"key":"alpha"}')]
                if self.calls == 1 else "value:alpha",
                usage=RequestUsage(prompt_tokens=10, completion_tokens=3), cached=False,
            )

        async def create_stream(self, *args, **kwargs):
            try:
                yield await self.create(*args, **kwargs)
            finally:
                self.streams_closed += 1

        @property
        def model_info(self):
            return {"vision": False, "function_calling": True, "json_output": False,
                    "family": "gpt-4o", "structured_output": False}

        @property
        def capabilities(self):
            return self.model_info

        def actual_usage(self):
            return RequestUsage(prompt_tokens=10 * self.calls, completion_tokens=3 * self.calls)

        def total_usage(self):
            return self.actual_usage()

        def count_tokens(self, *args, **kwargs):
            return 10

        def remaining_tokens(self, *args, **kwargs):
            return 1000

        async def close(self):
            self.closed = True

    results = {}

    async def run(mode):
        session = new_session(model="gpt-4o-mini", task=f"offline autogen {mode}", out_dir=root / mode)
        inner = OfflineClient()
        client = wrap_client(inner, session)

        if mode == "async":
            def lookup(key: str) -> str:
                """Look up a deterministic value."""
                return f"value:{key}"
        else:
            async def lookup(key: str) -> str:
                """Look up a deterministic value."""
                return f"value:{key}"

        agent = AssistantAgent("offline", model_client=client,
                               tools=[audit_tool(lookup, session)], reflect_on_tool_use=True,
                               model_client_stream=mode == "stream")
        try:
            if mode == "stream":
                output = [item async for item in agent.run_stream(task="lookup alpha")][-1]
            else:
                output = await agent.run(task="lookup alpha")
            assert output.messages[-1].content == "value:alpha", output
        finally:
            session.finish(success=True)
            await client.close()
        assert inner.closed
        if mode == "stream":
            assert inner.streams_closed == 2
        results[mode] = verify(session)
        assert inner.calls == 2

    for mode in ("async", "stream"):
        asyncio.run(run(mode))

    async def final_chunk_close():
        session = new_session(task="final chunk before close", out_dir=root / "early_close")
        inner = OfflineClient()
        client = wrap_client(inner, session)
        stream = client.create_stream([])
        try:
            await anext(stream)
            verify(session, turns=1, tools=0, prompt=10, completion=3)
        finally:
            await stream.aclose()
            session.finish(success=True)
            await client.close()
        assert inner.streams_closed == 1
        results["final_chunk_close"] = verify(session, turns=1, tools=0, prompt=10, completion=3)

    asyncio.run(final_chunk_close())
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", choices=["all", "crewai", "langgraph", "openai_agents", "autogen"],
                        default="all")
    parser.add_argument("--out-dir", type=Path, default=Path(".contextos/sdk-integration"))
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    os.environ.update({"OTEL_SDK_DISABLED": "true", "CREWAI_TELEMETRY_ENABLED": "false",
                       "CREWAI_TRACING_ENABLED": "false", "CONTEXTOS_QUIET": "1",
                       "PYTHON_DOTENV_DISABLED": "1"})
    import contextos_auditor

    checks = {"crewai": crewai_smoke, "langgraph": langgraph_smoke,
              "openai_agents": openai_agents_smoke, "autogen": autogen_smoke}
    report = {"auditor_source": str(Path(contextos_auditor.__file__).resolve()),
              "mode": "offline deterministic model; real SDK orchestration; no live LLM calls",
              "versions": {}, "results": {}}
    for package in ("crewai", "langgraph", "langchain-core", "openai-agents", "autogen-agentchat"):
        try:
            report["versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report["versions"][package] = "not installed"

    def require_loopback(address):
        try:
            if isinstance(address, tuple) and ipaddress.ip_address(address[0]).is_loopback:
                return
        except ValueError:
            pass
        raise AssertionError("Non-loopback network access forbidden by offline integration harness")

    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection

    def local_connect(sock, address):
        require_loopback(address)
        return original_connect(sock, address)

    def local_create_connection(address, *args, **kwargs):
        require_loopback(address)
        return original_create_connection(address, *args, **kwargs)

    # Windows asyncio's socketpair fallback needs an internal loopback connection.
    # Model traffic still uses an in-memory transport; external connects fail.
    with patch.object(socket.socket, "connect", local_connect), patch.object(
        socket, "create_connection", local_create_connection
    ):
        for name, check in checks.items():
            if args.framework not in ("all", name):
                continue
            try:
                report["results"][name] = {"status": "pass", "checks": check(args.out_dir / name)}
            except Exception:
                report["results"][name] = {"status": "fail", "error": traceback.format_exc()}
    assert socket.socket.connect is original_connect
    assert socket.create_connection is original_create_connection
    (args.out_dir / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return int(any(item["status"] != "pass" for item in report["results"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
