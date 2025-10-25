"""Simple CLI to query the local FAISS index built by the ingestion pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict

import faiss  # type: ignore
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer  # type: ignore
from opentelemetry import trace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
INDEX_PATH = PROJECT_ROOT / "artifacts" / "index.faiss"
METADATA_PATH = PROJECT_ROOT / "artifacts" / "chunk_metadata.jsonl"

# Ensure project root is on the import path when executed as a script.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from telemetry_utils import init_tracing


def load_config() -> Dict[str, Dict[str, str]]:
    import yaml

    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_metadata() -> Dict[int, Dict[str, str]]:
    mapping: Dict[int, Dict[str, str]] = {}
    with METADATA_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            mapping[int(record["faiss_id"])] = record
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the local FAISS index")
    parser.add_argument("query", help="Natural language question")
    parser.add_argument("--top-k", type=int, default=5, help="Number of hits to return")
    args = parser.parse_args()

    if not INDEX_PATH.exists():
        raise FileNotFoundError(f"FAISS index not found at {INDEX_PATH}. Run the ingestion flow first.")
    if not METADATA_PATH.exists():
        raise FileNotFoundError(f"Chunk metadata not found at {METADATA_PATH}.")

    cfg = load_config()
    load_dotenv()
    telemetry_cfg = cfg.get("telemetry", {})
    init_tracing("rag-query", telemetry_cfg)
    tracer = trace.get_tracer("rag.query")

    model_name = cfg["embedding"]["model"]
    metadata = load_metadata()
    index = faiss.read_index(str(INDEX_PATH))

    with tracer.start_as_current_span("query") as span:
        span.set_attribute("query.text", args.query)
        span.set_attribute("query.top_k", args.top_k)
        span.set_attribute("embedding.model", model_name)

        model = SentenceTransformer(model_name)

        with tracer.start_as_current_span("embed.query") as embed_span:
            query_vec = model.encode(
                [args.query],
                convert_to_numpy=True,
                normalize_embeddings=True,
            ).astype("float32")
            embed_span.set_attribute("embedding.dim", int(query_vec.shape[1]))

        with tracer.start_as_current_span("faiss.search") as search_span:
            scores, ids = index.search(query_vec, args.top_k)
            search_span.set_attribute("results.count", int(args.top_k))

        for rank, (score, internal_id) in enumerate(zip(scores[0], ids[0]), start=1):
            record = metadata.get(int(internal_id))
            if not record:
                continue
            preview = record.get("text", "").replace("\n", " ")
            if len(preview) > 300:
                preview = preview[:300] + "..."
            print(f"{rank}. score={score:.4f} chunk_id={record['chunk_id']} source={record['source_path']}")
            print(preview)
            print()
            span.add_event(
                "retrieval.result",
                {
                    "rank": int(rank),
                    "score": float(score),
                    "chunk_id": record.get("chunk_id"),
                    "source_path": record.get("source_path"),
                },
            )


if __name__ == "__main__":
    main()
