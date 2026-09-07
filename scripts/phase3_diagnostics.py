from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app
from pdm_intelligence.external.adapters import load_sql
from pdm_intelligence.external.gateway import DataGateway
from pdm_intelligence.external.model_lifecycle import predict_external_rul, train_external_rul
from pdm_intelligence.external.schema import canonicalize


def frames(n_assets: int = 10):
    rng = np.random.default_rng(20260817)
    assets = pd.DataFrame({
        "equipment_id": [f"PUMP-{i:02d}" for i in range(1, n_assets + 1)],
        "asset_type": ["process_pump"] * n_assets,
        "criticality": ["high" if i <= 4 else "medium" for i in range(1, n_assets + 1)],
    })
    telemetry = []
    maintenance = []
    for i in range(1, n_assets + 1):
        life = 34 + 4 * i
        degradation = 0.045 + 0.0045 * i
        for c in range(1, life + 1):
            telemetry.append({
                "equipment_id": f"PUMP-{i:02d}",
                "operating_cycle": c,
                "vibration_rms": 1.2 + degradation * c + rng.normal(0, 0.025),
                "bearing_temp": 61 + 0.30 * c + 1.25 * i + rng.normal(0, 0.25),
                "pressure": 9.4 - 0.018 * c - 0.03 * i + rng.normal(0, 0.02),
                "remaining_useful_life": life - c,
            })
        maintenance.append({
            "equipment_id": f"PUMP-{i:02d}",
            "maintenance_type": "corrective_failure",
            "downtime_hours": 5 + i / 3,
            "labor_hours": 7 + i / 2,
            "cost": 2500 + 180 * i,
            "failure": True,
        })
    return assets, pd.DataFrame(telemetry), pd.DataFrame(maintenance)


def _write_sqlite_fixture(path: Path, frame: pd.DataFrame) -> None:
    """Write a diagnostic SQLite fixture and release the Windows file handle deterministically."""
    with closing(sqlite3.connect(path)) as con:
        frame.to_sql("telemetry", con, index=False)
        con.commit()


def main():
    checks = {}
    details = {}
    with TemporaryDirectory(prefix="pdm-phase3-") as td:
        root = Path(td)
        assets, telemetry, maintenance = frames()
        gateway = DataGateway(root / "external", root / "catalog.db")
        result = gateway.ingest_frames(
            "Phase3 Generic Pump Fleet",
            {"assets": assets, "telemetry": telemetry, "maintenance": maintenance},
            source={"mode": "diagnostic_fixture", "evidence_class": "SYNTHETIC_VALIDATION"},
        )
        caps = result.readiness["capabilities"]
        checks["canonical_ingestion"] = result.entities["telemetry"]["rows"] == len(telemetry)
        checks["generic_condition_monitoring_ready"] = caps["condition_monitoring"]["status"] == "READY"
        checks["no_silent_fd001_transfer"] = caps["fd001_rul_inference"]["status"] == "NOT_READY"
        checks["external_training_ready"] = caps["external_rul_training"]["status"] == "READY"
        manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
        checks["content_fingerprint"] = len(manifest["fingerprint_sha256"]) == 64
        checks["evidence_boundary_recorded"] = "does not transfer NASA" in manifest["evidence_boundary"]

        replay = gateway.replay(result.dataset_id, upto=12, limit=10000)
        checks["replay_no_future_rows"] = max(x["cycle"] for x in replay["records"]) <= 12
        checks["replay_all_assets"] = replay["record_count"] == 10 * 12

        loaded = gateway.load_dataset(result.dataset_id)
        training = train_external_rul(
            loaded["telemetry"], result.dataset_id,
            output_root=root / "models", catalog_path=root / "catalog.db", seed=19,
        )
        details["external_model"] = training.to_dict()
        checks["asset_level_holdout"] = len(training.validation_assets) >= 2
        checks["model_beats_age_baseline"] = training.metrics["rmse"] < training.baseline_metrics["rmse"]
        predictions = predict_external_rul(loaded["telemetry"].drop(columns=["rul"]), training.model_id, root / "models")
        checks["external_inference"] = len(predictions) == 10 and all(x["predicted_rul"] >= 0 for x in predictions)
        checks["external_uncertainty_interval"] = all(x["rul_low_90"] <= x["predicted_rul"] <= x["rul_high_90"] for x in predictions)

        source_db = root / "source.db"
        canonical_tel = canonicalize(telemetry, "telemetry")
        _write_sqlite_fixture(source_db, canonical_tel.head(40))
        sql_frame = load_sql(f"sqlite:///{source_db.as_posix()}", table="telemetry")
        checks["sql_adapter"] = len(sql_frame) == 40 and "asset_id" in sql_frame
        # Windows will refuse this unlink if either sqlite3 or SQLAlchemy retained a file handle.
        source_db.unlink()
        checks["sql_source_handle_released"] = not source_db.exists()

        with TestClient(app) as client:
            checks["data_gateway_ui"] = client.get("/data-gateway").status_code == 200 and "NO SILENT MODEL TRANSFER" in client.get("/data-gateway").text
            checks["data_modes_api"] = client.get("/api/data/modes").status_code == 200

    out = {
        "phase": 3,
        "version": "1.0.0",
        "overall": "PASS" if all(checks.values()) else "FAIL",
        "evidence_class": "SYNTHETIC_VALIDATION_FOR_EXTERNAL_DATA_LIFECYCLE",
        "checks": checks,
        "details": details,
        "claim_boundary": "Diagnostic model metrics are synthetic pipeline validation, not field accuracy claims.",
    }
    artifact = Path("artifacts/phase3_diagnostics.json")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    raise SystemExit(0 if out["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
