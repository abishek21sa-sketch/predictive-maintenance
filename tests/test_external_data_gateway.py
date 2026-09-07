from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pdm_intelligence.external.adapters import load_sql
from pdm_intelligence.external.gateway import DataGateway
from pdm_intelligence.external.model_lifecycle import predict_external_rul, train_external_rul
from pdm_intelligence.external.readiness import assess_readiness
from pdm_intelligence.external.schema import canonicalize


def generic_frames(n_assets: int = 8):
    rng = np.random.default_rng(77)
    assets = pd.DataFrame({
        "Machine ID": [f"M-{i:02d}" for i in range(1, n_assets + 1)],
        "asset_type": ["compressor"] * n_assets,
        "criticality": ["high" if i <= 3 else "medium" for i in range(1, n_assets + 1)],
    })
    rows = []
    maintenance = []
    for i in range(1, n_assets + 1):
        life = 28 + i * 4
        slope = 0.05 + i * 0.004
        for cycle in range(1, life + 1):
            rul = life - cycle
            rows.append({
                "machine_id": f"M-{i:02d}",
                "operating_cycle": cycle,
                "vibration": 1.0 + slope * cycle + rng.normal(0, 0.02),
                "temperature": 65 + 0.32 * cycle + i * 1.7 + rng.normal(0, 0.2),
                "remaining_useful_life": rul,
            })
        maintenance.append({
            "machine_id": f"M-{i:02d}",
            "maintenance_type": "corrective_failure",
            "downtime_hours": 4 + i / 4,
            "labor_hours": 6 + i / 3,
            "cost": 1800 + 100 * i,
            "failure": True,
        })
    return assets, pd.DataFrame(rows), pd.DataFrame(maintenance)


def test_alias_mapping_and_canonical_validation():
    assets, telemetry, maintenance = generic_frames(4)
    a = canonicalize(assets, "assets")
    t = canonicalize(telemetry, "telemetry")
    m = canonicalize(maintenance, "maintenance")
    assert a.columns[0] == "asset_id"
    assert {"asset_id", "cycle", "rul", "vibration", "temperature"}.issubset(t.columns)
    assert {"asset_id", "event_type", "downtime_hours", "failure"}.issubset(m.columns)
    assert t.groupby("asset_id")["cycle"].apply(lambda s: s.is_monotonic_increasing).all()


def test_duplicate_telemetry_key_rejected():
    _, telemetry, _ = generic_frames(4)
    telemetry = pd.concat([telemetry, telemetry.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate telemetry"):
        canonicalize(telemetry, "telemetry")


def test_readiness_does_not_force_fd001_model_on_generic_equipment():
    assets, telemetry, maintenance = generic_frames(6)
    a = canonicalize(assets, "assets")
    t = canonicalize(telemetry, "telemetry")
    m = canonicalize(maintenance, "maintenance")
    r = assess_readiness(a, t, m)
    assert r["capabilities"]["condition_monitoring"]["status"] == "READY"
    assert r["capabilities"]["external_rul_training"]["status"] == "READY"
    assert r["capabilities"]["fd001_rul_inference"]["status"] == "NOT_READY"
    assert "sensor_21" in r["capabilities"]["fd001_rul_inference"]["missing"]


def test_gateway_persists_content_addressed_dataset_and_replay(tmp_path: Path):
    assets, telemetry, maintenance = generic_frames(6)
    gateway = DataGateway(tmp_path / "external", tmp_path / "catalog.db")
    result = gateway.ingest_frames("Compressor Fleet", {"assets": assets, "telemetry": telemetry, "maintenance": maintenance})
    assert result.dataset_id.startswith("compressor-fleet-")
    assert Path(result.manifest_path).exists()
    loaded = gateway.load_dataset(result.dataset_id)
    assert set(loaded) == {"assets", "telemetry", "maintenance"}
    replay = gateway.replay(result.dataset_id, upto=10, limit=10000)
    assert replay["evidence_class"] == "HISTORICAL_REPLAY"
    assert max(x["cycle"] for x in replay["records"]) <= 10
    assert replay["record_count"] == 6 * 10


def test_gateway_rejects_unknown_asset_reference(tmp_path: Path):
    assets, telemetry, _ = generic_frames(4)
    telemetry.loc[0, "machine_id"] = "M-999"
    gateway = DataGateway(tmp_path / "external", tmp_path / "catalog.db")
    with pytest.raises(ValueError, match="absent from the asset master"):
        gateway.ingest_frames("bad", {"assets": assets, "telemetry": telemetry})


def test_sql_adapter_reads_sqlite_table(tmp_path: Path):
    db = tmp_path / "source.db"
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE telemetry(machine_id TEXT, operating_cycle INTEGER, vibration REAL)")
        con.executemany("INSERT INTO telemetry VALUES(?,?,?)", [("A", 1, 1.2), ("A", 2, 1.4)])
        con.commit()
    df = load_sql(f"sqlite:///{db.as_posix()}", table="telemetry")
    assert len(df) == 2
    assert list(df.columns) == ["machine_id", "operating_cycle", "vibration"]


def test_external_rul_training_uses_asset_level_holdout_and_persists_model(tmp_path: Path):
    _, telemetry, _ = generic_frames(10)
    t = canonicalize(telemetry, "telemetry")
    result = train_external_rul(
        t,
        dataset_id="compressor-example-123",
        output_root=tmp_path / "models",
        catalog_path=tmp_path / "catalog.db",
        seed=11,
    )
    assert result.evidence_class == "VALIDATED_ON_USER_SUPPLIED_HOLDOUT"
    assert result.validation_assets
    assert len(result.validation_assets) >= 2
    assert result.promotion_gate["passed"] is True
    assert result.metrics["rmse"] < result.baseline_metrics["rmse"]
    assert (tmp_path / "models" / result.model_id / "model.joblib").exists()
    predicted = predict_external_rul(t.drop(columns=["rul"]), result.model_id, tmp_path / "models")
    assert len(predicted) == 10
    assert all(x["predicted_rul"] >= 0 for x in predicted)
    assert all(x["rul_low_90"] <= x["predicted_rul"] <= x["rul_high_90"] for x in predicted)


def test_training_rejects_too_few_asset_trajectories(tmp_path: Path):
    _, telemetry, _ = generic_frames(3)
    with pytest.raises(ValueError, match="At least 6"):
        train_external_rul(canonicalize(telemetry, "telemetry"), "tiny", tmp_path / "models", tmp_path / "cat.db")


def test_custom_column_mapping_is_honored():
    df = pd.DataFrame({"machine": ["A", "A"], "seq": [1, 2], "vib": [1.1, 1.2]})
    out = canonicalize(df, "telemetry", {"asset_id": "machine", "cycle": "seq"})
    assert list(out["asset_id"]) == ["A", "A"]
    assert list(out["cycle"]) == [1, 2]


def test_same_content_produces_same_dataset_id(tmp_path: Path):
    assets, telemetry, maintenance = generic_frames(5)
    gateway = DataGateway(tmp_path / "external", tmp_path / "catalog.db")
    a = gateway.ingest_frames("same", {"assets": assets, "telemetry": telemetry, "maintenance": maintenance})
    b = gateway.ingest_frames("same", {"assets": assets.copy(), "telemetry": telemetry.copy(), "maintenance": maintenance.copy()})
    assert a.dataset_id == b.dataset_id


def test_sql_manifest_never_persists_connection_credentials(tmp_path: Path):
    db = tmp_path / "source.db"
    with closing(sqlite3.connect(db)) as con:
        con.execute("CREATE TABLE telemetry(machine_id TEXT, operating_cycle INTEGER, vibration REAL)")
        con.executemany("INSERT INTO telemetry VALUES(?,?,?)", [("A", 1, 1.2), ("A", 2, 1.4)])
        con.commit()
    gateway = DataGateway(tmp_path / "external", tmp_path / "catalog.db")
    result = gateway.ingest_sql("sql safe", [{"entity_type": "telemetry", "connection_url": f"sqlite:///{db.as_posix()}", "table": "telemetry"}])
    text = Path(result.manifest_path).read_text(encoding="utf-8")
    assert "sqlite:///" not in text
    assert str(db) not in text
    assert '"mode": "sql"' in text


def test_timestamp_replay_accepts_utc_timestamp(tmp_path: Path):
    df = pd.DataFrame({
        "asset_id": ["A", "A", "A"],
        "timestamp": ["2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", "2026-01-01T02:00:00Z"],
        "vibration": [1.0, 1.1, 1.2],
    })
    gateway = DataGateway(tmp_path / "external", tmp_path / "catalog.db")
    result = gateway.ingest_frames("timestamped", {"telemetry": df})
    replay = gateway.replay(result.dataset_id, upto="2026-01-01T01:00:00Z")
    assert replay["record_count"] == 2


def test_external_feature_engineering_excludes_target_like_leakage_columns(tmp_path: Path):
    _, telemetry, _ = generic_frames(8)
    t = canonicalize(telemetry, "telemetry")
    t["failure_cycle"] = t.groupby("asset_id")["cycle"].transform("max")
    result = train_external_rul(t, "leakage-guard", tmp_path / "models", tmp_path / "catalog.db", seed=4)
    assert "failure_cycle" not in result.features


def test_file_path_manifest_redacts_absolute_local_path(tmp_path: Path):
    source = tmp_path / "private" / "machine_telemetry.csv"
    source.parent.mkdir()
    source.write_text("asset_id,cycle,vibration\nA,1,1.1\nA,2,1.2\n", encoding="utf-8")
    gateway = DataGateway(tmp_path / "external", tmp_path / "catalog.db")
    result = gateway.ingest_paths("path safe", [{"entity_type": "telemetry", "path": str(source)}])
    text = Path(result.manifest_path).read_text(encoding="utf-8")
    assert str(source.parent) not in text
    assert "machine_telemetry.csv" in text
    assert '"local_path_redacted": true' in text
