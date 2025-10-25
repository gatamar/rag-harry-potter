-- Retrieval ledger schema (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY,
    trace_id TEXT,
    user_query TEXT NOT NULL,
    normalized_query TEXT,
    embedding_model TEXT,
    embedding_checksum TEXT,
    query_vector_norm REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidate_sets (
    id INTEGER PRIMARY KEY,
    query_id INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    params TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    candidate_set_id INTEGER NOT NULL REFERENCES candidate_sets(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    chunk_id TEXT NOT NULL,
    score REAL,
    score_components TEXT,
    selected INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1))
);

CREATE TABLE IF NOT EXISTS chunk_provenance (
    chunk_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    item_id TEXT,
    spine_index INTEGER,
    paragraph_offset INTEGER,
    chunk_text_hash TEXT,
    parser_version TEXT
);

CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY,
    query_id INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    response_text TEXT,
    token_count INTEGER,
    latency_ms INTEGER,
    feedback TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidate_sets_query_stage
    ON candidate_sets (query_id, stage);

CREATE INDEX IF NOT EXISTS idx_candidates_chunk
    ON candidates (chunk_id);

CREATE INDEX IF NOT EXISTS idx_queries_created_at
    ON queries (created_at DESC);

CREATE VIEW IF NOT EXISTS latest_queries AS
SELECT
    q.id AS query_id,
    q.created_at,
    q.user_query,
    q.normalized_query,
    a.response_text,
    a.latency_ms,
    cs.stage,
    c.rank,
    c.chunk_id,
    c.score,
    c.selected
FROM queries q
LEFT JOIN answers a ON a.query_id = q.id
LEFT JOIN candidate_sets cs ON cs.query_id = q.id
LEFT JOIN candidates c ON c.candidate_set_id = cs.id;
