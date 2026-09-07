from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from pdm_intelligence.storage.database import connect_state, ensure_schema_mutation_allowed

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id INTEGER NOT NULL,
  cycle INTEGER NOT NULL,
  rul_cycles REAL NOT NULL,
  anomaly_score REAL NOT NULL,
  failure_risk REAL NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id INTEGER NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLiteStore:
    def __init__(self, path: str | Path):
        if not ensure_schema_mutation_allowed(path):
            self.path = Path(path)
            return
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_state(self.path)) as con:
            con.executescript(SCHEMA)
            con.commit()

    def save_prediction(self, asset_id, cycle, rul, anomaly, risk):
        with closing(connect_state(self.path)) as con:
            con.execute(
                "INSERT INTO predictions(asset_id,cycle,rul_cycles,anomaly_score,failure_risk) VALUES(?,?,?,?,?)",
                (int(asset_id), int(cycle), float(rul), float(anomaly), float(risk)),
            )
            con.commit()

    def save_decision(self, decision: dict):
        with closing(connect_state(self.path)) as con:
            con.execute("INSERT INTO decisions(asset_id,payload) VALUES(?,?)", (int(decision["asset_id"]), json.dumps(decision)))
            con.commit()
