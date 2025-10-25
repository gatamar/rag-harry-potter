# Telemetry Setup (Jaeger)

The pipeline emits OpenTelemetry spans via OTLP gRPC to a local Jaeger collector.

## Install Jaeger (macOS)

- Download the latest Jaeger backend bundle from the Jaeger releases page and place the contents under `tools/jaeger/`, **or**
- Run the official Docker image: `docker run --rm -it -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one:latest --collector.otlp.grpc.enabled=true --collector.otlp.grpc.host-port=:4317`

> Homebrew no longer ships a Jaeger formula. Use the binary or Docker options above.

### macOS Gatekeeper note

If you download the macOS binary directly, Gatekeeper may block it with “Apple could not verify…”. You can allow it via:

```bash
xattr -d com.apple.quarantine tools/jaeger/jaeger-bundle-*/jaeger
```

Alternatively, after the first blocked launch, open **System Settings → Privacy & Security** and choose **Allow Anyway** for the Jaeger binary, then rerun the command.

## Run the collector/UI

Use the config ports defined in `config.yaml` (`telemetry.otlp_grpc_port`, `telemetry.jaeger_ui_port`).

Jaeger v2 expects a configuration file. Use the local config at `telemetry/jaeger-config.yaml`:

```bash
./tools/jaeger/jaeger-all-in-one --config=file:telemetry/jaeger-config.yaml
```

Then open the UI at `http://localhost:16686`.

## Verify spans

1. Start Jaeger as above.
2. Run the ingestion flow (`python flows/rag_pipeline.py`) or the query CLI (`python tools/query.py "<question>"`).
3. In the Jaeger UI, select the appropriate service (`rag-ingestion` for the flow, `rag-query` for the CLI) and search for recent traces.

Each trace contains spans for the major pipeline stages (parsing, embedding, FAISS search). Use the span attributes to inspect chunk counts, embedding configuration, and retrieved chunk metadata during debugging.
