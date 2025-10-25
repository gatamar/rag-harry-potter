"""
Utility helpers to configure OpenTelemetry tracing for local runs.

This module centralises tracer initialisation so both the ingestion flow and
CLI tools use the same Jaeger OTLP endpoint derived from config.yaml.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

_LOGGER = logging.getLogger(__name__)
_TRACER_INITIALISED = False


def init_tracing(service_name: str, telemetry_cfg: Dict[str, Any]) -> None:
    """
    Initialise the global tracer provider with an OTLP exporter aimed at the
    Jaeger collector described in the telemetry configuration.

    Subsequent calls are no-ops so the first caller defines the service name.
    """
    global _TRACER_INITIALISED
    if _TRACER_INITIALISED:
        return

    endpoint = _build_endpoint(telemetry_cfg)
    try:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    except Exception as exc:  # pragma: no cover - defensive fallback
        _LOGGER.warning("Failed to initialise OTLP exporter (%s); tracing disabled", exc)
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _TRACER_INITIALISED = True


def _build_endpoint(telemetry_cfg: Dict[str, Any]) -> str:
    host = telemetry_cfg.get("otlp_host", "127.0.0.1")
    port = telemetry_cfg.get("otlp_grpc_port", 4317)
    return f"{host}:{port}"
