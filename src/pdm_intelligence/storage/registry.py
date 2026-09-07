from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from pdm_intelligence.storage.database import connect_state, ensure_schema_mutation_allowed

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets(
  asset_id INTEGER PRIMARY KEY,
  asset_type TEXT NOT NULL,
  status TEXT NOT NULL,
  installed_cycle INTEGER NOT NULL DEFAULT 0,
  metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS feature_store(
  asset_id INTEGER NOT NULL,
  cycle INTEGER NOT NULL,
  feature_set_version TEXT NOT NULL,
  payload TEXT NOT NULL,
  PRIMARY KEY(asset_id, cycle, feature_set_version)
);
"""


class AssetRegistry:
    def __init__(self, path: str | Path):
        if not ensure_schema_mutation_allowed(path):
            self.path = Path(path)
            return
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_state(self.path)) as con:
            con.executescript(SCHEMA)
            con.commit()

    def upsert(self, asset_id: int, asset_type: str = "turbofan", status: str = "active", installed_cycle: int = 0, metadata: dict | None = None):
        with closing(connect_state(self.path)) as con:
            con.execute(
                "INSERT INTO assets(asset_id,asset_type,status,installed_cycle,metadata) VALUES(?,?,?,?,?) "
                "ON CONFLICT(asset_id) DO UPDATE SET asset_type=excluded.asset_type,status=excluded.status,installed_cycle=excluded.installed_cycle,metadata=excluded.metadata",
                (asset_id, asset_type, status, installed_cycle, json.dumps(metadata or {})),
            )
            con.commit()

    def list_assets(self) -> list[dict]:
        with closing(connect_state(self.path)) as con:
            rows = con.execute("SELECT asset_id,asset_type,status,installed_cycle,metadata FROM assets ORDER BY asset_id").fetchall()
        return [{"asset_id":r[0],"asset_type":r[1],"status":r[2],"installed_cycle":r[3],"metadata":json.loads(r[4])} for r in rows]


class FeatureStore:
    def __init__(self, path: str | Path):
        if not ensure_schema_mutation_allowed(path):
            self.path = Path(path)
            return
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_state(self.path)) as con:
            con.executescript(SCHEMA)
            con.commit()

    def put(self, asset_id: int, cycle: int, features: dict, version: str = "v1"):
        with closing(connect_state(self.path)) as con:
            con.execute(
                "INSERT INTO feature_store(asset_id,cycle,feature_set_version,payload) VALUES(?,?,?,?) "
                "ON CONFLICT(asset_id,cycle,feature_set_version) DO UPDATE SET payload=excluded.payload",
                (asset_id, cycle, version, json.dumps(features)),
            )
            con.commit()

    def get(self, asset_id: int, cycle: int, version: str = "v1") -> dict | None:
        with closing(connect_state(self.path)) as con:
            row = con.execute("SELECT payload FROM feature_store WHERE asset_id=? AND cycle=? AND feature_set_version=?", (asset_id,cycle,version)).fetchone()
        return json.loads(row[0]) if row else None
