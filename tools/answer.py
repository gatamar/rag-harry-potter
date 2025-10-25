"""LangChain-based CLI to generate local RAG answers."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import faiss  # type: ignore
from dotenv import load_dotenv
from langchain.prompts import ChatPromptTemplate
from langchain.schema import StrOutputParser
from langchain_openai import ChatOpenAI
from opentelemetry import trace
from sentence_transformers import SentenceTransformer  # type: ignore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import telemetry_utils  # noqa: E402

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CHAPTERS_JSONL = PROJECT_ROOT / "artifacts" / "chapters.jsonl"
METADATA_JSONL = PROJECT_ROOT / "artifacts" / "chunk_metadata.jsonl"
FAISS_INDEX_PATH = PROJECT_ROOT / "artifacts" / "index.faiss"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "rag_answer.md"
LEDGER_DB_PATH = PROJECT_ROOT / "artifacts" / "retrieval_ledger.db"


def load_config() -> Dict[str, Any]:
    import yaml

    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_chunk_records() -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    with CHAPTERS_JSONL.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            records[record["chunk_id"]] = record
    return records


def load_metadata() -> Dict[int, Dict[str, Any]]:
    mapping: Dict[int, Dict[str, Any]] = {}
    with METADATA_JSONL.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            mapping[int(record["faiss_id"])] = record
    return mapping


def load_prompt() -> ChatPromptTemplate:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return ChatPromptTemplate.from_template(template)


def ensure_artifacts_exist() -> None:
    for path in (CHAPTERS_JSONL, METADATA_JSONL, FAISS_INDEX_PATH, PROMPT_PATH):
        if not path.exists():
            raise FileNotFoundError(f"Required artifact missing: {path}")


def retrieve_chunks(
    question: str,
    top_k: int,
    embedder: SentenceTransformer,
    index: faiss.Index,
    metadata: Dict[int, Dict[str, Any]],
    chunk_records: Dict[str, Dict[str, Any]],
    tracer: trace.Tracer,
) -> List[Dict[str, Any]]:
    with tracer.start_as_current_span("embed.query") as span:
        query_vec = embedder.encode(
            [question],
            convert_to_numpy=True,
            normalize_embeddings=False,
        ).astype("float32")
        span.set_attribute("embedding.dim", int(query_vec.shape[1]))

    faiss.normalize_L2(query_vec)

    with tracer.start_as_current_span("faiss.search") as span:
        scores, ids = index.search(query_vec, top_k)
        span.set_attribute("results.count", int(top_k))

    hits: List[Dict[str, Any]] = []
    for rank, (score, internal_id) in enumerate(zip(scores[0], ids[0]), start=1):
        if internal_id == -1:
            continue
        meta = metadata.get(int(internal_id))
        if not meta:
            continue
        chunk_id = meta["chunk_id"]
        record = chunk_records.get(chunk_id)
        if not record:
            continue
        hit = {
            "rank": rank,
            "score": float(score),
            "chunk_id": chunk_id,
            "text": record["text"],
            "source_path": meta.get("source_path"),
            "spine_index": meta.get("spine_index"),
        }
        hits.append(hit)

    return hits


def build_context(hits: List[Dict[str, Any]]) -> str:
    segments = []
    for hit in hits:
        header = f"Chunk {hit['chunk_id']} (score={hit['score']:.4f}):"
        segments.append(header)
        segments.append(hit["text"])
        segments.append("")
    return "\n".join(segments).strip()


def run_chain(
    question: str,
    context: str,
    provider: str,
    model_name: str,
    tracer: trace.Tracer,
) -> str:
    prompt = load_prompt()

    if provider.lower() != "openai":
        raise ValueError(f"Unsupported LLM provider: {provider}")

    llm = ChatOpenAI(model=model_name)
    chain = prompt | llm | StrOutputParser()

    with tracer.start_as_current_span("llm.answer") as span:
        span.set_attribute("llm.provider", provider)
        span.set_attribute("llm.model", model_name)
        response = chain.invoke({"context": context, "question": question})
        span.add_event("answer.generated", {"answer": response})

    return response


def write_ledger(
    question: str,
    hits: List[Dict[str, Any]],
    answer: str,
    config: Dict[str, Any],
    trace_id_hex: str,
) -> None:
    ledger_path = LEDGER_DB_PATH
    if not ledger_path.exists():
        return

    conn = sqlite3.connect(str(ledger_path))
    conn.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO queries(trace_id, user_query, normalized_query, embedding_model, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                trace_id_hex,
                question,
                question,
                config["embedding"]["model"],
                now,
            ),
        )
        query_id = cur.lastrowid

        params = {
            "top_k": len(hits),
            "model": config["embedding"]["model"],
        }
        cur.execute(
            """
            INSERT INTO candidate_sets(query_id, stage, generated_at, params)
            VALUES (?, ?, ?, ?)
            """,
            (query_id, "retriever", now, json.dumps(params)),
        )
        candidate_set_id = cur.lastrowid

        for hit in hits:
            cur.execute(
                """
                INSERT INTO candidates(candidate_set_id, rank, chunk_id, score, score_components, selected)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_set_id,
                    hit["rank"],
                    hit["chunk_id"],
                    hit["score"],
                    json.dumps({"retriever": hit["score"]}),
                    1 if hit["rank"] == 1 else 0,
                ),
            )

        cur.execute(
            """
            INSERT INTO answers(query_id, response_text, token_count, latency_ms)
            VALUES (?, ?, ?, ?)
            """,
            (query_id, answer, None, None),
        )
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a local RAG answer via LangChain")
    parser.add_argument("question", help="Natural language question")
    parser.add_argument("--top-k", type=int, default=None, help="Override retriever top-K")
    args = parser.parse_args()

    load_dotenv()
    ensure_artifacts_exist()
    config = load_config()

    top_k = args.top_k or int(config.get("retrieval", {}).get("top_k", 5))

    telemetry_cfg = config.get("telemetry", {})
    telemetry_utils.init_tracing("rag-langchain", telemetry_cfg)
    tracer = trace.get_tracer("rag.answer")

    embedder = SentenceTransformer(config["embedding"]["model"])
    index = faiss.read_index(str(FAISS_INDEX_PATH))
    metadata = load_metadata()
    chunk_records = load_chunk_records()

    with tracer.start_as_current_span("answer.session") as root_span:
        hits = retrieve_chunks(
            args.question,
            top_k,
            embedder,
            index,
            metadata,
            chunk_records,
            tracer,
        )

        context = build_context(hits)
        answer = run_chain(
            args.question,
            context,
            config["llm"]["provider"],
            config["llm"]["model"],
            tracer,
        )

        span_context = root_span.get_span_context()
        trace_id_hex = f"{span_context.trace_id:032x}" if span_context.is_valid else ""
        write_ledger(args.question, hits, answer, config, trace_id_hex)

    print(answer)
    if hits:
        print("\nContext chunks:")
        for hit in hits:
            print(f"- {hit['chunk_id']} (score={hit['score']:.4f})")


if __name__ == "__main__":
    main()
