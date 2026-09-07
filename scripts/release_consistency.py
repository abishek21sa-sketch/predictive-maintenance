"""Verify that published FD001 evidence and bundled runtime metadata agree."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRIC_KEYS = ("mae", "rmse", "r2", "nasa_score")


def load_json(relative: str) -> dict:
    path = ROOT / relative
    if not path.exists():
        raise FileNotFoundError(relative)
    return json.loads(path.read_text(encoding="utf-8"))


def compare_metrics(left: dict, right: dict, *, tolerance: float = 1e-10) -> list[str]:
    errors: list[str] = []
    for key in METRIC_KEYS:
        if key not in left or key not in right:
            errors.append(f"missing_metric:{key}")
            continue
        if abs(float(left[key]) - float(right[key])) > tolerance:
            errors.append(f"metric_mismatch:{key}")
    return errors


def check() -> dict[str, object]:
    metadata = load_json("src/pdm_intelligence/release/fd001_model_metadata.json")
    release = load_json("artifacts/reports/fd001_release_evidence.json")
    benchmark = load_json("artifacts/reports/fd001_benchmark.json")
    snapshot = load_json("artifacts/reports/fd001_operational_snapshot.json")
    manifest = load_json("RELEASE_MANIFEST.json")

    errors: list[str] = []
    if metadata.get("dataset") != "NASA C-MAPSS FD001":
        errors.append("metadata_dataset_mismatch")
    if release.get("dataset") != metadata.get("dataset"):
        errors.append("release_dataset_mismatch")
    if benchmark.get("dataset") != metadata.get("dataset"):
        errors.append("benchmark_dataset_mismatch")
    if snapshot.get("dataset") != metadata.get("dataset"):
        errors.append("snapshot_dataset_mismatch")

    selected_model = metadata.get("selected_model")
    for name, payload in (("release", release), ("benchmark", benchmark), ("snapshot", snapshot)):
        if payload.get("selected_model") != selected_model:
            errors.append(f"{name}_selected_model_mismatch")

    canonical_metrics = metadata.get("official_test_metrics", {})
    for name, payload in (
        ("release", release),
        ("benchmark", benchmark),
        ("snapshot", {"official_test_metrics": snapshot.get("model_metrics", {})}),
    ):
        errors.extend(f"{name}_{error}" for error in compare_metrics(canonical_metrics, payload.get("official_test_metrics", {})))

    manifest_ai = manifest.get("ai", {})
    manifest_expectations = {
        "fd001_selected_model": selected_model,
        "fd001_mae": round(float(canonical_metrics.get("mae", float("nan"))), 2),
        "fd001_rmse": round(float(canonical_metrics.get("rmse", float("nan"))), 2),
        "fd001_r2": round(float(canonical_metrics.get("r2", float("nan"))), 3),
    }
    for key, expected in manifest_expectations.items():
        if manifest_ai.get(key) != expected:
            errors.append(f"manifest_mismatch:{key}")

    if snapshot.get("asset_count") != 100:
        errors.append("snapshot_asset_count_mismatch")
    if metadata.get("uncertainty_method") != "asset_holdout_empirical_residual_band_90":
        errors.append("uncertainty_method_mismatch")
    if not metadata.get("residual_interval_90", 0) > 0:
        errors.append("uncertainty_interval_missing")

    return {
        "status": "PASS" if not errors else "FAIL",
        "selected_model": selected_model,
        "official_test_metrics": canonical_metrics,
        "checked_sources": [
            "src/pdm_intelligence/release/fd001_model_metadata.json",
            "artifacts/reports/fd001_release_evidence.json",
            "artifacts/reports/fd001_benchmark.json",
            "artifacts/reports/fd001_operational_snapshot.json",
            "RELEASE_MANIFEST.json",
        ],
        "errors": errors,
        "claim_boundary": (
            "This gate verifies evidence lineage and publication consistency. "
            "It does not certify model quality, field reliability, or production readiness."
        ),
    }


def main() -> None:
    result = check()
    out = ROOT / "artifacts" / "release_consistency.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
