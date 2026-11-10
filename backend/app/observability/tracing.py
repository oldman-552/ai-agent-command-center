"""OpenTelemetry tracing (UX-05). Spans for every HTTP request (FastAPI instrumentation), every execution run and
every bus message handled. Exported over OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (any collector: Jaeger,
Tempo, Honeycomb…); otherwise tracing still runs (trace ids appear in the JSON logs) but nothing leaves the process.
Tests attach an in-memory exporter with `add_exporter`."""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

_provider: TracerProvider | None = None


def setup_tracing(service: str) -> TracerProvider:
    global _provider
    if _provider is not None:
        return _provider
    _provider = TracerProvider(resource=Resource.create({"service.name": f"acc-{service}",
                                                         "service.namespace": "ai-agent-command-center"}))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_provider)
    return _provider


def add_exporter(exporter: SpanExporter) -> None:
    """Synchronous export (tests, debugging)."""
    setup_tracing("api").add_span_processor(SimpleSpanProcessor(exporter))


def tracer():
    return trace.get_tracer("acc")


def instrument_app(app) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app, excluded_urls="api/health")
