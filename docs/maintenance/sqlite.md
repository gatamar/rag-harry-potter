# SQLite Ledger Maintenance

The retrieval ledger lives in `artifacts/retrieval_ledger.db`.
Use the SQLite CLI for quick inspection:

1. Launch: `sqlite3 artifacts/retrieval_ledger.db`
2. List tables/views: `.tables`
   - Expected tables/views: `queries`, `candidate_sets`, `candidates`, `chunk_provenance`, `answers`, `latest_queries`
3. Ad-hoc queries:
   - `SELECT * FROM latest_queries LIMIT 5;`
   - `SELECT chunk_id, score FROM candidates ORDER BY score DESC LIMIT 10;`
4. View schema: `.schema queries`
5. Exit: `.exit`

For GUI exploration, open the same file with DB Browser for SQLite, TablePlus, or a similar tool.
