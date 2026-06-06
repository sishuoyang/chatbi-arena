"""OpenTelemetry setup for ChatBI Arena components.

Exports OTLP/gRPC to a local ClickStack OTel collector (default localhost:4317),
which writes traces/metrics/logs into the ClickHouse Cloud service. Set
OTEL_EXPORTER_OTLP_ENDPOINT to override; set OTEL_SDK_DISABLED=true to no-op.

Usage:
    from observability.instrumentation import init_telemetry
    tracer, meter = init_telemetry("arena-datagen")
    with tracer.start_as_current_span("seed"):
        ...
    rows_counter = meter.create_counter("arena.rows_written")
    rows_counter.add(120, {"table": "orders"})
"""
import os
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

_INITIALISED: dict[str, tuple] = {}


def init_telemetry(service_name: str):
    """Idempotent per service_name. Returns (tracer, meter)."""
    if service_name in _INITIALISED:
        return _INITIALISED[service_name]

    if os.environ.get("OTEL_SDK_DISABLED", "").lower() == "true":
        t, m = trace.get_tracer(service_name), metrics.get_meter(service_name)
        _INITIALISED[service_name] = (t, m)
        return t, m

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    resource = Resource.create({"service.name": service_name})

    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(tp)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, insecure=True), export_interval_millis=5000)
    mp = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(mp)

    pair = (trace.get_tracer(service_name), metrics.get_meter(service_name))
    _INITIALISED[service_name] = pair
    return pair
