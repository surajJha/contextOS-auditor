"""Optional OpenTelemetry GenAI-semconv span export (AUD-012).

Every framework adapter always writes the local JSONL trail on its own --
that is the tool's zero-setup, zero-dependency, offline-first core and is
never affected by anything in this module. This module adds a second,
purely additive, opt-in export path: real OTel spans (following the OTel
GenAI semantic conventions, `gen_ai.*` attributes) sent to an OTLP/HTTP
endpoint the caller names -- so a team with an existing Datadog/Grafana/
Honeycomb collector can see these agent runs show up there too, without
giving up the local-only default for everyone who doesn't opt in.

Opt-in triggers (either one enables it):
  - `attach(..., otel_endpoint="http://localhost:4318/v1/traces")`
  - `CONTEXTOS_OTEL_ENDPOINT=http://localhost:4318/v1/traces` env var

If the optional `opentelemetry-sdk` / `opentelemetry-exporter-otlp-proto-http`
packages aren't installed (`pip install contextos-auditor[otel]`), or span
creation/export ever raises for any reason, this degrades to a single
one-time `UserWarning` and a permanent no-op for the rest of the process --
matching `base.py`'s `guarded()` contract: an observability add-on must
never be able to break the real agent run it's watching.
"""

from __future__ import annotations

import warnings
from typing import Any

_otel_warned = False


def _warn_otel_unavailable(exc: Exception) -> None:
    global _otel_warned
    if _otel_warned:
        return
    _otel_warned = True
    warnings.warn(
        "contextos-auditor: an OTel export endpoint was configured but the "
        "optional OTel packages aren't usable here -- run "
        "`pip install contextos-auditor[otel]`. OTel export is disabled for "
        "this process; your local .contextos/audit JSONL recording is "
        f"completely unaffected ({exc.__class__.__name__}: {exc}).",
        stacklevel=3,
    )


class OtelExporter:
    """One instance per `FrameworkAuditSession`. `enabled` is False (pure
    no-op) whenever OTel isn't configured/available -- every call site below
    checks it first so the hot path costs nothing when this feature isn't
    in use, which is the common case."""

    def __init__(self, endpoint: str, *, framework: str) -> None:
        self._framework = framework
        self._tracer: Any = None
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create({"service.name": "contextos-auditor"})
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
            )
            self._tracer = trace.get_tracer("contextos_auditor", tracer_provider=provider)
        except Exception as exc:  # noqa: BLE001 -- see module docstring
            _warn_otel_unavailable(exc)
            self._tracer = None

    @property
    def enabled(self) -> bool:
        return self._tracer is not None

    def export_llm_turn(
        self,
        *,
        turn: int,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        estimated_usd: float | None,
    ) -> None:
        if not self.enabled:
            return
        try:
            with self._tracer.start_as_current_span(f"gen_ai.chat #{turn}") as span:
                span.set_attribute("gen_ai.system", self._framework)
                span.set_attribute("gen_ai.operation.name", "chat")
                span.set_attribute("gen_ai.request.model", model)
                span.set_attribute("gen_ai.response.model", model)
                span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
                span.set_attribute("gen_ai.usage.total_tokens", total_tokens)
                if estimated_usd is not None:
                    span.set_attribute("contextos.estimated_usd", estimated_usd)
        except Exception as exc:  # noqa: BLE001 -- see module docstring
            _warn_otel_unavailable(exc)

    def export_tool_call(self, *, name: str, turn: int) -> None:
        if not self.enabled:
            return
        try:
            with self._tracer.start_as_current_span(f"gen_ai.execute_tool #{turn}") as span:
                span.set_attribute("gen_ai.system", self._framework)
                span.set_attribute("gen_ai.operation.name", "execute_tool")
                span.set_attribute("gen_ai.tool.name", name)
        except Exception as exc:  # noqa: BLE001 -- see module docstring
            _warn_otel_unavailable(exc)


def build_exporter(endpoint: str | None, *, framework: str) -> OtelExporter | None:
    """Returns None (not a disabled OtelExporter) when no endpoint is
    configured at all, so `FrameworkAuditSession` can skip even
    constructing one -- keeps `otel_endpoint=None` (the default for every
    existing caller) exactly as cheap as before this feature existed."""
    if not endpoint:
        return None
    return OtelExporter(endpoint, framework=framework)
