"""
Prefect flow orchestrating the RAG ingestion pipeline as outlined in design.md.

The flow currently wires the high-level stages described in the design document:
1. Ensure the artifacts directory exists.
2. Parse the EPUB into chapter-level JSONL.
3. Build the FAISS index from the JSONL using SentenceTransformers embeddings.
4. Apply the SQLite schema for the retrieval ledger.

Steps (2) and (3) require dedicated utilities that are not yet implemented in the
repository. They are represented as Prefect tasks that currently raise
NotImplementedError so the missing components are obvious when the flow runs.
Update those tasks once the corresponding scripts/utilities exist.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

import yaml  # Requires PyYAML.
from prefect import flow, task, get_run_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "schema" / "retrieval_ledger.sql"


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
    """
    Placeholder for the parsing utility.

    The design specifies producing chapter-level JSONL at
    config(artifacts.chapters_jsonl) using ebooklib + BeautifulSoup.
    Implement the actual parsing command/script and update this task
    accordingly.
    """
    target_jsonl = PROJECT_ROOT / config["artifacts"]["chapters_jsonl"]
    raise NotImplementedError(
        (
            "Parsing step not implemented. Produce JSONL at "
            f"{target_jsonl} according to design.md (ebooklib + BeautifulSoup)."
        )
    )


@task
def build_faiss_index(config: Dict[str, Any], _) -> Path:
    """
    Placeholder for the index build utility (batch workflow).

    The design references running:
      python tools/build_index.py --input artifacts/chapters.jsonl --output artifacts/index.faiss
    and persisting companion metadata.
    Replace this stub once the build script exists.
    """
    chapters_jsonl = PROJECT_ROOT / config["artifacts"]["chapters_jsonl"]
    index_file = PROJECT_ROOT / config["artifacts"]["index_file"]
    raise NotImplementedError(
        (
            "Index build step not implemented. Create FAISS index at "
            f"{index_file} from {chapters_jsonl} with the SentenceTransformers workflow."
        )
    )


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

    ensure_artifacts_dir(config)
    chapters_jsonl = parse_epub_to_jsonl(config)
    build_faiss_index(config, chapters_jsonl)
    apply_ledger_schema(config)


if __name__ == "__main__":
    rag_ingestion_pipeline()
