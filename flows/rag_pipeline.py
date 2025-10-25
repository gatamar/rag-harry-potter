"""
Prefect flow orchestrating the RAG ingestion pipeline as outlined in design.md.

The flow currently wires the high-level stages described in the design document:
1. Ensure the artifacts directory exists.
2. Parse the EPUB into chapter-level JSONL.
3. Build the FAISS index from the JSONL using SentenceTransformers embeddings.
4. Apply the SQLite schema for the retrieval ledger.

The parsing task is implemented inline according to design.md. The FAISS build step
still expects a dedicated utility; its task currently raises NotImplementedError to
highlight the missing component.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

import hashlib
import json
import numpy as np
import yaml  # Requires PyYAML.
from prefect import flow, task, get_run_logger
from opentelemetry import trace

import faiss  # type: ignore
from bs4 import BeautifulSoup  # type: ignore
from ebooklib import ITEM_DOCUMENT, epub  # type: ignore
from sentence_transformers import SentenceTransformer  # type: ignore

from telemetry_utils import init_tracing

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "schema" / "retrieval_ledger.sql"
EPUB_PATH = PROJECT_ROOT / "raw-data" / "book.epub"
PARSER_VERSION = "0.1.0"


def _chunk_paragraphs(
    paragraphs: list[Dict[str, Any]],
    chunk_target: int,
    chunk_min: int,
    chunk_overlap: int,
) -> list[list[Dict[str, Any]]]:
    chunks: list[list[Dict[str, Any]]] = []
    total = len(paragraphs)
    start = 0

    while start < total:
        end = start
        tokens = 0
        current: list[Dict[str, Any]] = []

        while end < total and (tokens < chunk_target or not current):
            segment = paragraphs[end]
            current.append(segment)
            tokens += segment["token_count"]
            end += 1

        while tokens < chunk_min and end < total:
            segment = paragraphs[end]
            current.append(segment)
            tokens += segment["token_count"]
            end += 1

        chunks.append(current)

        if end >= total:
            break

        if chunk_overlap <= 0:
            start = end
        else:
            tokens_back = 0
            back_idx = end - 1
            while back_idx >= start and tokens_back < chunk_overlap:
                tokens_back += paragraphs[back_idx]["token_count"]
                back_idx -= 1
            start = max(back_idx + 1, start + 1)

    return chunks


def _load_chunk_records(jsonl_path: Path) -> list[Dict[str, Any]]:
    records: list[Dict[str, Any]] = []
    with jsonl_path.open("r", encoding="utf-8") as infile:
        for line in infile:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def load_config() -> Dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)


@task
def ensure_artifacts_dir(config: Dict[str, Any]) -> Path:
    artifacts_dir = PROJECT_ROOT / config["artifacts"]["dir"]
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return artifacts_dir


@task
def parse_epub_to_jsonl(config: Dict[str, Any]) -> Path:
    tracer = trace.get_tracer("rag.ingestion")
    logger = get_run_logger()

    if not EPUB_PATH.exists():
        raise FileNotFoundError(f"EPUB source not found at {EPUB_PATH}.")

    target_jsonl = PROJECT_ROOT / config["artifacts"]["chapters_jsonl"]
    target_jsonl.parent.mkdir(parents=True, exist_ok=True)

    chunk_target = int(config["chunking"]["target_tokens"])
    chunk_min = int(config["chunking"]["min_tokens"])
    chunk_overlap = int(config["chunking"]["overlap_tokens"])

    with tracer.start_as_current_span("parse_epub_to_jsonl") as span:
        span.set_attribute("chunk.target_tokens", int(chunk_target))
        span.set_attribute("chunk.min_tokens", int(chunk_min))
        span.set_attribute("chunk.overlap_tokens", int(chunk_overlap))

        book = epub.read_epub(str(EPUB_PATH))
        spine_item_ids = [item_id for item_id, _ in book.spine]
        span.set_attribute("chapters.total", int(len(spine_item_ids)))

        records = []
        for spine_index, item_id in enumerate(spine_item_ids):
            item = book.get_item_with_id(item_id)
            if item is None or item.get_type() != ITEM_DOCUMENT:
                continue

            html_bytes = item.get_content()
            html_checksum = hashlib.sha256(html_bytes).hexdigest()
            soup = BeautifulSoup(html_bytes, "lxml")

            # Drop style/script tags.
            for bad_tag in soup.find_all(["style", "script"]):
                bad_tag.decompose()

            chapter_title = ""
            for heading in soup.find_all(["h1", "h2", "h3"]):
                text = heading.get_text(" ", strip=True)
                if text:
                    chapter_title = text
                    break
            if not chapter_title:
                chapter_title = f"Chapter {spine_index + 1}"

            paragraphs = []
            char_cursor = 0
            block_tags = ["h1", "h2", "h3", "p", "li", "blockquote"]
            body = soup.body or soup
            for node in body.find_all(block_tags):
                text = " ".join(node.stripped_strings)
                if not text:
                    continue
                token_count = len(text.split())
                paragraphs.append(
                    {
                        "index": len(paragraphs),
                        "text": text,
                        "offset": char_cursor,
                        "token_count": token_count,
                    }
                )
                char_cursor += len(text) + 1  # simple newline separator

            if not paragraphs:
                continue

            chapter_records = _chunk_paragraphs(
                paragraphs=paragraphs,
                chunk_target=chunk_target,
                chunk_min=chunk_min,
                chunk_overlap=chunk_overlap,
            )

            for chunk_idx, chunk in enumerate(chapter_records):
                chunk_id = f"{item.get_name()}-{chunk_idx:04d}"
                chunk_text = "\n\n".join(seg["text"] for seg in chunk)
                chunk_tokens = sum(seg["token_count"] for seg in chunk)
                chunk_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()

                record = {
                    "chunk_id": chunk_id,
                    "chapter_title": chapter_title,
                    "item_id": item_id,
                    "spine_index": spine_index,
                    "chunk_index": chunk_idx,
                    "source_path": item.get_name(),
                    "chapter_html_checksum": html_checksum,
                    "parser_version": PARSER_VERSION,
                    "token_count": chunk_tokens,
                    "text": chunk_text,
                    "paragraph_indices": [seg["index"] for seg in chunk],
                    "paragraph_offsets": [seg["offset"] for seg in chunk],
                    "chunk_text_hash": chunk_hash,
                }
                records.append(record)

        with target_jsonl.open("w", encoding="utf-8") as output_file:
            for record in records:
                output_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        span.set_attribute("chunks.total", int(len(records)))

    logger.info("Parsed %s chunks into %s", len(records), target_jsonl)
    return target_jsonl


@task
def build_faiss_index(config: Dict[str, Any], chapters_jsonl: Path) -> Path:
    tracer = trace.get_tracer("rag.ingestion")
    logger = get_run_logger()

    if chapters_jsonl is None:
        chapters_jsonl = PROJECT_ROOT / config["artifacts"]["chapters_jsonl"]
    chapters_jsonl = Path(chapters_jsonl)
    if not chapters_jsonl.exists():
        raise FileNotFoundError(
            f"Chunk JSONL not found at {chapters_jsonl}. Run parsing step first."
        )

    embedding_cfg = config.get("embedding")
    if not embedding_cfg or "model" not in embedding_cfg:
        raise ValueError(
            "Missing embedding configuration. Define embedding.model (and optional "
            "embedding.batch_size) in config.yaml."
        )

    model_name = embedding_cfg["model"]
    batch_size = int(embedding_cfg.get("batch_size", 32))

    index_path = PROJECT_ROOT / config["artifacts"]["index_file"]
    metadata_path = PROJECT_ROOT / config["artifacts"]["chunk_metadata_jsonl"]
    index_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)

    with tracer.start_as_current_span("build_faiss_index") as span:
        span.set_attribute("embedding.model", model_name)
        span.set_attribute("embedding.batch_size", int(batch_size))
        span.set_attribute("index.output_path", str(index_path))

        logger.info(
            "Loading chapter chunks from %s and embedding with %s (batch_size=%s)",
            chapters_jsonl,
            model_name,
            batch_size,
        )

        chunk_records = _load_chunk_records(chapters_jsonl)
        if not chunk_records:
            raise ValueError(f"No chunk records found in {chapters_jsonl}.")
        span.set_attribute("chunks.total", int(len(chunk_records)))

        texts = [record["text"] for record in chunk_records]

        model = SentenceTransformer(model_name)
        with tracer.start_as_current_span("embedding.encode") as encode_span:
            embeddings = model.encode(
                texts,
                batch_size=batch_size,
                convert_to_numpy=True,
                show_progress_bar=True,
                normalize_embeddings=False,
            ).astype("float32")
            encode_span.set_attribute("embeddings.count", int(embeddings.shape[0]))

        with tracer.start_as_current_span("faiss.index_build") as index_span:
            faiss.normalize_L2(embeddings)
            dim = embeddings.shape[1]
            base_index = faiss.IndexFlatIP(dim)
            index = faiss.IndexIDMap(base_index)
            ids = np.arange(len(embeddings), dtype=np.int64)
            index.add_with_ids(embeddings, ids)
            faiss.write_index(index, str(index_path))
            index_span.set_attribute("faiss.dim", int(dim))

        with metadata_path.open("w", encoding="utf-8") as metadata_file:
            for internal_id, record in zip(ids.tolist(), chunk_records, strict=True):
                metadata_record = {
                    "faiss_id": internal_id,
                    "chunk_id": record["chunk_id"],
                    "chunk_text_hash": record.get("chunk_text_hash"),
                    "source_path": record.get("source_path"),
                    "item_id": record.get("item_id"),
                    "spine_index": record.get("spine_index"),
                    "chunk_index": record.get("chunk_index"),
                    "token_count": record.get("token_count"),
                    "parser_version": record.get("parser_version"),
                }
                metadata_file.write(json.dumps(metadata_record, ensure_ascii=False) + "\n")

        logger.info(
            "Built FAISS index with %s vectors (dim=%s) at %s and metadata at %s",
            len(chunk_records),
            dim,
            index_path,
            metadata_path,
        )
        span.set_attribute("faiss.dim", int(dim))
        span.set_attribute("faiss.index_path", str(index_path))
        span.set_attribute("metadata.path", str(metadata_path))
        return index_path


@task
def apply_ledger_schema(config: Dict[str, Any]) -> Path:
    logger = get_run_logger()
    ledger_path = PROJECT_ROOT / config["artifacts"]["ledger_db"]
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file missing: {SCHEMA_PATH}")

    with SCHEMA_PATH.open("r", encoding="utf-8") as schema_file:
        schema_sql = schema_file.read()

    with sqlite3.connect(str(ledger_path)) as connection:
        connection.executescript(schema_sql)
        connection.commit()

    logger.info("Applied retrieval ledger schema to %s", ledger_path)
    return ledger_path


@flow(name="rag_ingestion_pipeline")
def rag_ingestion_pipeline() -> None:
    config = load_config()
    telemetry_cfg = config.get("telemetry", {})
    init_tracing("rag-ingestion", telemetry_cfg)
    tracer = trace.get_tracer("rag.ingestion")

    with tracer.start_as_current_span("pipeline.run"):
        ensure_artifacts_dir(config)
        chapters_jsonl = parse_epub_to_jsonl(config)
        build_faiss_index(config, chapters_jsonl)
        apply_ledger_schema(config)


if __name__ == "__main__":
    rag_ingestion_pipeline()
