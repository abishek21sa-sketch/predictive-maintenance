from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any

from pdm_intelligence.storage.database import connect_state, ensure_schema_mutation_allowed

SCHEMA = """
CREATE TABLE IF NOT EXISTS external_datasets(
  dataset_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  evidence_class TEXT NOT NULL,
  manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_models(
  model_id TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  manifest_json TEXT NOT NULL
);
"""


class ExternalCatalog:
    def __init__(self, path: str | Path):
        if not ensure_schema_mutation_allowed(path):
            self.path = Path(path)
            return
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_state(self.path)) as con:
            con.executescript(SCHEMA)
            con.commit()

    def put_dataset(self, dataset_id: str, name: str, manifest: dict[str, Any], evidence_class: str = "USER_SUPPLIED_DATA") -> None:
        with closing(connect_state(self.path)) as con:
            con.execute(
                "INSERT INTO external_datasets(dataset_id,name,evidence_class,manifest_json) VALUES(?,?,?,?) "
                "ON CONFLICT(dataset_id) DO UPDATE SET name=excluded.name,evidence_class=excluded.evidence_class,manifest_json=excluded.manifest_json",
                (dataset_id, name, evidence_class, json.dumps(manifest, sort_keys=True)),
            )
            con.commit()

    def list_datasets(self) -> list[dict[str, Any]]:
        with closing(connect_state(self.path)) as con:
            rows = con.execute(
                "SELECT dataset_id,name,created_at,evidence_class,manifest_json FROM external_datasets ORDER BY created_at DESC"
            ).fetchall()
        return [
            {"dataset_id": r[0], "name": r[1], "created_at": r[2], "evidence_class": r[3], **json.loads(r[4])}
            for r in rows
        ]

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with closing(connect_state(self.path)) as con:
            row = con.execute(
                "SELECT dataset_id,name,created_at,evidence_class,manifest_json FROM external_datasets WHERE dataset_id=?",
                (dataset_id,),
            ).fetchone()
        if not row:
            return None
        return {"dataset_id": row[0], "name": row[1], "created_at": row[2], "evidence_class": row[3], **json.loads(row[4])}

    def put_model(self, model_id: str, dataset_id: str, manifest: dict[str, Any]) -> None:
        with closing(connect_state(self.path)) as con:
            con.execute(
                "INSERT INTO external_models(model_id,dataset_id,manifest_json) VALUES(?,?,?) "
                "ON CONFLICT(model_id) DO UPDATE SET dataset_id=excluded.dataset_id,manifest_json=excluded.manifest_json",
                (model_id, dataset_id, json.dumps(manifest, sort_keys=True)),
            )
            con.commit()

    def list_models(self) -> list[dict[str, Any]]:
        with closing(connect_state(self.path)) as con:
            rows = con.execute(
                "SELECT model_id,dataset_id,created_at,manifest_json FROM external_models ORDER BY created_at DESC"
            ).fetchall()
        return [
            {"model_id": r[0], "dataset_id": r[1], "created_at": r[2], **json.loads(r[3])}
            for r in rows
        ]
