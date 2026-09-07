from __future__ import annotations

import hashlib
import json

from pdm_intelligence.real_data.metropt import (
    METROPT_RUNTIME_INTEGRITY_SCHEMA_VERSION,
    verify_metropt_runtime_integrity,
)


def _descriptor(root, relative_path):
    path = root / relative_path
    return {
        "relative_path": relative_path,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _runtime_manifest(root):
    for relative_path in (
        "derived/metropt_feature_store.csv.gz",
        "models/metropt_failure_horizon.joblib",
        "models/metropt_anomaly.joblib",
        "models/metropt_holdout_scores.csv.gz",
    ):
        (root / relative_path).parent.mkdir(parents=True, exist_ok=True)
        (root / relative_path).write_bytes(relative_path.encode("utf-8"))
    runtime = {
        "integrity": {
            "schema_version": METROPT_RUNTIME_INTEGRITY_SCHEMA_VERSION,
            "feature_store": _descriptor(root, "derived/metropt_feature_store.csv.gz"),
            "model_artifacts": {
                "failure_horizon_model": _descriptor(root, "models/metropt_failure_horizon.joblib"),
                "anomaly_model": _descriptor(root, "models/metropt_anomaly.joblib"),
                "holdout_scores": _descriptor(root, "models/metropt_holdout_scores.csv.gz"),
            },
        }
    }
    runtime_path = root / "derived/metropt_runtime.json"
    runtime_bytes = json.dumps(runtime, indent=2, sort_keys=True).encode("utf-8")
    runtime_path.write_bytes(runtime_bytes)
    (root / "derived/metropt_runtime.sha256").write_text(
        hashlib.sha256(runtime_bytes).hexdigest() + "\n",
        encoding="utf-8",
    )
    return runtime


def test_runtime_integrity_passes_for_bound_artifacts(tmp_path):
    runtime = _runtime_manifest(tmp_path)
    result = verify_metropt_runtime_integrity(tmp_path, runtime)
    assert result["status"] == "PASS"
    assert result["passed"] is True
    assert len(result["checks"]) == 5


def test_runtime_integrity_rejects_modified_artifact(tmp_path):
    runtime = _runtime_manifest(tmp_path)
    (tmp_path / "models/metropt_anomaly.joblib").write_bytes(b"modified")
    result = verify_metropt_runtime_integrity(tmp_path, runtime)
    assert result["passed"] is False
    assert "model:anomaly_model:sha256_mismatch" in result["errors"]


def test_runtime_integrity_rejects_modified_runtime_manifest(tmp_path):
    runtime = _runtime_manifest(tmp_path)
    runtime_path = tmp_path / "derived/metropt_runtime.json"
    runtime_path.write_text(
        runtime_path.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": 2'),
        encoding="utf-8",
    )
    result = verify_metropt_runtime_integrity(tmp_path, runtime)
    assert result["passed"] is False
    assert "runtime_manifest:sha256_mismatch" in result["errors"]


def test_runtime_integrity_rejects_path_escape_and_missing_manifest(tmp_path):
    missing = verify_metropt_runtime_integrity(tmp_path, {})
    assert missing["status"] == "MISSING"
    runtime = {
        "integrity": {
            "schema_version": METROPT_RUNTIME_INTEGRITY_SCHEMA_VERSION,
            "feature_store": {"relative_path": "../outside.bin", "sha256": "x"},
        }
    }
    escaped = verify_metropt_runtime_integrity(tmp_path, runtime)
    assert "feature_store:path_escapes_runtime_root" in escaped["errors"]
