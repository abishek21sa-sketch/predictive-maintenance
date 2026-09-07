import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pdm_intelligence.models.model_registry import ModelRegistry
from pdm_intelligence.security.deployment_evidence import (
    SCHEMA_VERSION,
    validate_deployment_evidence,
)
from pdm_intelligence.storage.managed_state import (
    describe_database_url,
    managed_state_declared,
)
from scripts import enterprise_acceptance
from scripts.backup_restore import backup_database
from scripts.load_smoke import _percentile, run_load


def test_managed_database_descriptor_redacts_credentials():
    descriptor = describe_database_url("postgresql+psycopg://operator:super-secret@db.example:5432/pdm")
    assert descriptor.valid is True
    assert descriptor.backend == "managed"
    assert descriptor.redacted_url == "postgresql+psycopg://db.example:5432/pdm"
    assert "super-secret" not in json.dumps(descriptor.to_dict())
    assert describe_database_url("sqlite:///data/pdm.db").valid is False


def test_managed_state_requires_explicit_adapter_declaration(monkeypatch):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.delenv("PDM_MANAGED_STATE_ADAPTER", raising=False)
    assert managed_state_declared() is False
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    assert managed_state_declared() is True


def test_deployment_evidence_requires_all_controls_and_digest():
    controls = {
        name: {
            "status": "verified",
            "owner": "platform",
            "verified_at": "2026-09-05T12:00:00Z",
            "evidence_ref": f"evidence://{name}",
        }
        for name in (
            "production_auth",
            "managed_state_backend",
            "tls_termination",
            "observability",
            "backup_and_restore",
            "site_connector",
            "change_control",
        )
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "release_id": "1.0.0",
        "environment": "staging",
        "artifact_sha256": "a" * 64,
        "attested_by": "platform",
        "attested_at": "2026-09-05T12:00:00Z",
        "controls": controls,
    }
    first = validate_deployment_evidence(payload)
    assert first["status"] == "INVALID"
    assert "missing_evidence_digest" in first["errors"]
    payload["evidence_digest"] = first["evidence_digest"]
    report = validate_deployment_evidence(payload)
    assert report["status"] == "PASS_ATTESTATION"
    assert len(report["verified_controls"]) == 7


def test_deployment_evidence_requires_valid_artifact_digest():
    payload = {
        "schema_version": SCHEMA_VERSION,
        "release_id": "1.0.0",
        "environment": "staging",
        "attested_by": "platform",
        "attested_at": "2026-09-05T12:00:00Z",
        "controls": {},
    }

    report = validate_deployment_evidence(payload)

    assert "missing_artifact_sha256" in report["errors"]


def test_deployment_evidence_rejects_raw_secret_material():
    report = validate_deployment_evidence({"schema_version": SCHEMA_VERSION, "api_key": "do-not-store"})
    assert report["status"] == "INVALID"
    assert "raw_secret_material_detected" in report["errors"]


def test_deployment_evidence_rejects_stale_timestamps_and_non_uri_references():
    stale = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "release_id": "1.0.0",
        "environment": "staging",
        "attested_by": "platform",
        "attested_at": stale,
        "controls": {
            name: {
                "status": "verified",
                "owner": "platform",
                "verified_at": stale,
                "evidence_ref": "operator-record",
            }
            for name in (
                "production_auth",
                "managed_state_backend",
                "tls_termination",
                "observability",
                "backup_and_restore",
                "site_connector",
                "change_control",
            )
        },
    }
    report = validate_deployment_evidence(payload)
    assert report["status"] == "INVALID"
    assert "attested_at_timestamp_expired" in report["errors"]
    assert "invalid_production_auth_evidence_ref" in report["errors"]


def test_deployment_evidence_rejects_secret_bearing_reference():
    payload = {
        "schema_version": SCHEMA_VERSION,
        "release_id": "1.0.0",
        "environment": "staging",
        "attested_by": "platform",
        "attested_at": datetime.now(UTC).isoformat(),
        "controls": {
            name: {
                "status": "verified",
                "owner": "platform",
                "verified_at": datetime.now(UTC).isoformat(),
                "evidence_ref": f"evidence://{name}",
            }
            for name in (
                "production_auth",
                "managed_state_backend",
                "tls_termination",
                "observability",
                "backup_and_restore",
                "site_connector",
                "change_control",
            )
        },
    }
    payload["controls"]["production_auth"]["evidence_ref"] = "vault://prod/auth?token=redacted"
    report = validate_deployment_evidence(payload)
    assert report["status"] == "INVALID"
    assert "secret_bearing_production_auth_evidence_ref" in report["errors"]

    payload["controls"]["production_auth"]["evidence_ref"] = "vault://prod/auth?%74oken=redacted"
    encoded_report = validate_deployment_evidence(payload)
    assert encoded_report["status"] == "INVALID"
    assert "secret_bearing_production_auth_evidence_ref" in encoded_report["errors"]


def test_deployment_evidence_template_is_not_ready():
    template = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "deployment_evidence.template.json")
        .read_text(encoding="utf-8")
    )
    report = validate_deployment_evidence(template)
    assert report["status"] == "INVALID"
    assert "control_not_verified_production_auth" in report["errors"]


def test_model_quality_lane_rejects_stale_diagnostics(tmp_path, monkeypatch):
    runtime_root = tmp_path / "data" / "external" / "metropt3"
    runtime_path = runtime_root / "derived" / "metropt_runtime.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text("trusted-runtime", encoding="utf-8")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "phase5_diagnostics.json").write_text(
        json.dumps({"runtime_evidence_sha256": "0" * 64}), encoding="utf-8"
    )
    monkeypatch.setattr(enterprise_acceptance, "ROOT", tmp_path)
    monkeypatch.setattr(
        "pdm_intelligence.real_data.metropt.load_metropt_runtime",
        lambda root: {
            "integrity_check": {"passed": True},
            "model_status": "PROMOTION_READY",
            "model": {"quality_gate": {}},
        },
    )

    report = enterprise_acceptance._model_quality_lane()

    assert report["status"] == "FAIL"
    assert report["errors"] == ["diagnostics_runtime_evidence_sha256_mismatch"]


def test_model_registry_requires_gate_and_human_promotion(tmp_path):
    root = tmp_path / "models"
    model_id = "rul-candidate-001"
    model_dir = root / model_id
    model_dir.mkdir(parents=True)
    (model_dir / "model.joblib").write_bytes(b"model-artifact")
    (model_dir / "manifest.json").write_text(json.dumps({
        "model_id": model_id,
        "dataset_id": "dataset-001",
        "features": ["sensor"],
        "selected_model": "ridge",
        "metrics": {"rmse": 10.0},
        "baseline_metrics": {"rmse": 20.0},
        "promotion_gate": {
            "version": 2,
            "passed": True,
            "rule": "RMSE and MAE beat baseline",
            "metric_comparison": {
                "rmse_beats_baseline": True,
                "mae_beats_baseline": True,
                "selected_rmse": 10.0,
                "baseline_rmse": 20.0,
                "selected_mae": 5.0,
                "baseline_mae": 10.0,
            },
            "validation_design": "asset-level holdout",
            "validation_asset_count": 2,
            "asset_disjoint": True,
            "target_excluded_from_features": True,
        },
        "evidence_class": "VALIDATED_ON_USER_SUPPLIED_HOLDOUT",
    }), encoding="utf-8")
    registry = ModelRegistry(root)
    candidate = registry.register(model_id)
    assert candidate["stage"] == "candidate"
    approved = registry.promote(model_id, actor="approver-1", reason="holdout gate reviewed")
    assert approved["stage"] == "approved"
    assert registry.get(model_id)["approved_by"] == "approver-1"


def test_model_registry_blocks_failed_gate(tmp_path):
    root = tmp_path / "models"
    model_id = "rul-candidate-failed"
    model_dir = root / model_id
    model_dir.mkdir(parents=True)
    (model_dir / "model.joblib").write_bytes(b"model-artifact")
    (model_dir / "manifest.json").write_text(json.dumps({
        "model_id": model_id,
        "dataset_id": "dataset-001",
        "features": ["sensor"],
        "metrics": {"rmse": 30.0},
        "baseline_metrics": {"rmse": 20.0},
    }), encoding="utf-8")
    registry = ModelRegistry(root)
    registry.register(model_id)
    with pytest.raises(ValueError, match="validation gate"):
        registry.promote(model_id, actor="approver-1", reason="reviewed")


def test_model_registry_blocks_manifest_tamper_after_registration(tmp_path):
    root = tmp_path / "models"
    model_id = "rul-candidate-tampered"
    model_dir = root / model_id
    model_dir.mkdir(parents=True)
    (model_dir / "model.joblib").write_bytes(b"model-artifact")
    manifest = {
        "model_id": model_id,
        "dataset_id": "dataset-001",
        "features": ["sensor"],
        "selected_model": "ridge",
        "metrics": {"rmse": 10.0},
        "baseline_metrics": {"rmse": 20.0},
        "promotion_gate": {
            "version": 2,
            "passed": True,
            "rule": "RMSE and MAE beat baseline",
            "metric_comparison": {
                "rmse_beats_baseline": True,
                "mae_beats_baseline": True,
                "selected_rmse": 10.0,
                "baseline_rmse": 20.0,
                "selected_mae": 5.0,
                "baseline_mae": 10.0,
            },
            "validation_design": "asset-level holdout",
            "validation_asset_count": 2,
            "asset_disjoint": True,
            "target_excluded_from_features": True,
        },
    }
    manifest_path = model_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    registry = ModelRegistry(root)
    registry.register(model_id)
    manifest["metrics"]["rmse"] = 11.0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="changed after registration"):
        registry.promote(model_id, actor="approver-1", reason="reviewed")


def test_backup_database_verifies_content(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "backup.db"
    with closing(sqlite3.connect(source)) as con:
        con.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        con.execute("INSERT INTO evidence(value) VALUES ('verified')")
        con.commit()
    result = backup_database(source, destination)
    assert result["status"] == "PASS_REFERENCE_ONLY"
    assert result["backup_fingerprint"]["rows:evidence"] == 1


def test_load_smoke_reports_latency_and_status(monkeypatch):
    monkeypatch.setattr("scripts.load_smoke._request", lambda url, timeout: (200, 0.1, None))
    result = run_load("http://example.test/health", requests=8, concurrency=2)
    assert result["status"] == "PASS"
    assert result["status_counts"] == {"200": 8}
    assert _percentile([1.0, 2.0, 3.0], 0.5) == 2.0
