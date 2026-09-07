from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pandas as pd


def load_text(content: str, filename: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(StringIO(content))
    if suffix in {".json", ".jsonl", ".ndjson"}:
        if suffix in {".jsonl", ".ndjson"}:
            return pd.read_json(StringIO(content), lines=True)
        payload = json.loads(content)
        if isinstance(payload, dict):
            payload = payload.get("records", payload.get("data", payload))
        return pd.DataFrame(payload)
    raise ValueError("Browser text ingestion supports CSV, JSON, JSONL and NDJSON")


def load_path(path: str | Path) -> pd.DataFrame:
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_file():
        raise ValueError(f"Input file does not exist: {p}")
    suffix = p.suffix.lower()
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(p)
    if suffix in {".json", ".jsonl", ".ndjson"}:
        return pd.read_json(p, lines=suffix in {".jsonl", ".ndjson"})
    if suffix in {".parquet", ".pq"}:
        try:
            return pd.read_parquet(p)
        except ImportError as exc:
            raise ValueError(
                "Parquet ingestion requires an installed parquet engine. Install the project with the 'parquet' extra."
            ) from exc
    raise ValueError(f"Unsupported file type: {suffix}")


def load_sql(connection_url: str, table: str | None = None, query: str | None = None) -> pd.DataFrame:
    if not table and not query:
        raise ValueError("SQL ingestion requires table or query")
    try:
        from sqlalchemy import create_engine, inspect, text
    except ImportError as exc:
        raise ValueError("SQL ingestion requires SQLAlchemy") from exc
    engine = create_engine(connection_url)
    try:
        if query:
            statement = text(query)
        else:
            inspector = inspect(engine)
            if table not in inspector.get_table_names():
                raise ValueError(f"SQL table not found: {table}")
            safe = (table or "").replace('"', '""')
            statement = text(f'SELECT * FROM "{safe}"')
        with engine.connect() as con:
            return pd.read_sql_query(statement, con)
    finally:
        engine.dispose()
