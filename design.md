# High-level requirements
Build the RAG for the `../raw-data/book.epub` using such stack:
    + LangChain for LLM decoder wrapper
    + Python
    + FAISS for vector embedding
    + SentenceTransformers
    + OpenTelemetry for tracing

Such that in the initial version the retriever is dense, but sparse retriever will be added in the next versions.

One the main tenets for this RAG implementation is extreme support for debugging and troubleshooting. If a sub-optimal chunk is retrieved as a result for similarity search for a given query, I must be able to quickly understand which part of the pipeline fails.

## Parameters
- Use `config.yaml` as the single source of truth; refer to settings inline via `config(section.key)`.

## Pipeline architecture

### Ingestion and parsing (EPUB via `ebooklib` + `BeautifulSoup`)
- Spine iteration: use `ebooklib.epub.read_epub()` to resolve the manifest and spine order, skipping non-content assets; treat each `index_split_XX.html` item as a chapter payload and capture `item.get_id()` for provenance.
- HTML cleanup: run each HTML blob through `BeautifulSoup("lxml")`, strip `<style>`/Calibre-specific classes, convert headings (`h1`/`h2`/`h3`) to normalized section titles, collapse extra whitespace, and preserve inline emphasis tags when useful.
- Provenance metadata: emit structured records per chapter with fields such as `source_path`, `item_id`, `chapter_title`, `spine_index`, and checksum/hash of the raw HTML to align with tracing logs and retrieval ledger entries; persist as chapter-level JSONL (one record per line) to keep parsing output inspectable alongside the retrieval ledger.
- Paragraph extraction: collect text nodes under block-level elements (`p`, `li`, heading siblings); batch consecutive paragraphs into chunks targeting `config(chunking.target_tokens)` tokens, allowing truncation down to `config(chunking.min_tokens)` when context is short, and advance through the chapter with a sliding overlap of `config(chunking.overlap_tokens)` tokens to preserve dialogue continuity across chunk boundaries.

### Embedding and index build (JSONL ➜ SentenceTransformers ➜ FAISS)
- Input dataset: read the chapter-level JSONL (`config(artifacts.chapters_jsonl)`) and flatten into chunk records `{chunk_id, chapter_title, paragraph_offset, text, metadata}` so provenance ties back to parsing artifacts.
- Embedding configuration: load the SentenceTransformers model specified by `config(embedding.model)` (batch size `config(embedding.batch_size)`) with tracing spans around batching; record `embedding_model`, `embedding_dim`, and `batch_size` for each run in structured logs and the retrieval ledger.
- Metadata enrichment: attach `parser_version`, `chunk_text_hash`, `spine_index`, and chunk length metrics to each embedding to support downstream filtering and debugging.
- FAISS index artifacts: build a dense index (`IndexFlatIP` initially) and persist both the `config(artifacts.index_file)` artifact and a `config(artifacts.chunk_metadata_jsonl)` companion that maps FAISS ids to chunk metadata hashes for replay.
- Instrumentation hooks: emit spans `embedding.batch` and `faiss.build` with counters (`chunks_total`, `tokens_total`) and include the resulting index checksum in the ledger so retriever queries map to the exact index version.

#### Option 1: Batch rebuild workflow
- Trigger: run `python tools/build_index.py --input {config(artifacts.chapters_jsonl)} --output {config(artifacts.index_file)}`.
- Steps: load all JSONL records into memory, chunk with the configured sliding window, embed in CPU batches via `torch` (no GPU expected), build FAISS index from all vectors, snapshot outputs (index + metadata + run config JSON).
- Pros: simple, reproducible, easy to diff by rerunning; works offline and integrates well with CI smoke tests.
- Cons: requires full re-embed on corpus changes; higher memory footprint during build.

- Rebuild cadence: rerun the batch workflow whenever you adjust parsing, chunking, or embedding parameters, or whenever the source corpus changes; otherwise the existing index remains valid.

### Retrieval ledger plan
- Storage: persist per-query artifacts in a local SQLite database (`config(artifacts.ledger_db)`) to enable ad-hoc SQL inspection and joins with parsing outputs.
- Logging flow: on each query, record normalization inputs/outputs, embedding metadata (model name, checksum), candidate rankings before/after rerank, final answer references, and tracing span ids for cross-correlation.
- Access patterns: expose a CLI helper (e.g., `python tools/ledger.py show --last {config(retrieval_ledger.default_show_limit)}`) and SQL views for common diagnostics such as “top rejected candidates” or “queries with low score spread”.

#### SQL schema
- See `schema/retrieval_ledger.sql` for the canonical table, index, and view definitions used by the SQLite ledger.

### Answer synthesis (LangChain circuit)
- Retriever wiring: load the FAISS index (`config(artifacts.index_file)`) with LangChain’s `FAISS.load_local`, hydrate metadata from `config(artifacts.chunk_metadata_jsonl)`, and expose a retriever configured with `search_kwargs={"k": config(retrieval.top_k)}`.
- Prompt template: store the answer template at `config(artifacts.prompt_file)`; include instructions to cite `chunk_id` provenance alongside the response and surface empty-context warnings.
- LLM client: instantiate a chat model through LangChain (`langchain-openai`, `langchain-anthropic`, etc.) using `config(llm.provider)` to select the adapter and `config(llm.model)` for the concrete checkpoint; keep credentials local via environment variables.
- Secrets handling: load provider credentials (e.g., `OPENAI_API_KEY`) from a local `.env` file using `python-dotenv` so secrets never touch source control.
- Chain composition: build an LCEL pipeline that maps `{"context": retriever, "question": identity}` into the prompt, applies the LLM, and parses the output text; wrap each stage in OpenTelemetry spans (`rag.query.embed`, `rag.query.search`, `rag.query.answer`).
- Ledger hook: after every invocation, write the query, top-`k` results (scores + chunk ids), and final answer into the SQLite ledger using the schema above so retrieval issues can be replayed.
- CLI entrypoint: provide `python tools/answer.py "<question>"` that loads config, initialises tracing, runs the LCEL chain, prints both the answer and trace id, and optionally dumps chunk previews for debugging.

## Observability and debugging
- Structured logs: emit JSON logs per pipeline stage (ingestion, chunking, embedding, indexing, retrieval, rerank, answering) with a shared `trace_id`, stage name, key inputs, output hashes, latency, and artifact versions; store them locally (e.g., rotating file handler) for replay and comparison.
- OpenTelemetry without Docker:
    - install `opentelemetry-sdk`, `opentelemetry-api`, and required instrumentation packages inside the Python virtualenv
    - configure a `TracerProvider` with `BatchSpanProcessor`
    - install Jaeger locally by downloading the release bundle and placing it under `tools/jaeger/`, or run the official Docker image
    - add an `OTLPSpanExporter` pointing at local Jaeger (`localhost:config(telemetry.otlp_grpc_port)`, launched via `./tools/jaeger/jaeger-all-in-one --config=file:telemetry/jaeger-config.yaml`)
    - wrap each pipeline stage in spans documenting parameters, score distributions, and failure flags
    - inspect traces via the Jaeger UI
    - example Jaeger launch command: `./tools/jaeger/jaeger-all-in-one --config=file:telemetry/jaeger-config.yaml` (OTLP ingest enabled) and open `http://localhost:config(telemetry.jaeger_ui_port)` (Jaeger UI)
- Retrieval ledger: persist a per-query JSON or SQLite record capturing query normalization results, embedding vector checksum, ranked candidate list before/after rerank, similarity scores, reranker adjustments, and chunk provenance (source doc, offsets, chunking strategy, embedding model version); expose a lightweight CLI endpoint (defaulting to `config(retrieval_ledger.default_show_limit)` rows) to inspect these ledgers during debugging sessions.

## Future improvements
- Optional annotations: record TOC references via `book.get_toc()` to map chapters to human-readable anchors; include footnotes/endnotes handling if present by linking backlinks in the metadata record for later UI rendering.
