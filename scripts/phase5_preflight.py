from __future__ import annotations

import json
import os
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from package_release import package_repository

from pdm_intelligence.api.main import app
from pdm_intelligence.real_data.metropt import (
    ANALOG_COLUMNS,
    DIGITAL_COLUMNS,
    validate_metropt3_csv,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    excerpt = ROOT / "tests" / "fixtures" / "metropt_real_excerpt.csv"
    parsed = validate_metropt3_csv(excerpt, strict_full_dataset=False, chunksize=3)
    old_root = os.environ.get("PDM_METROPT_ROOT")
    try:
        with tempfile.TemporaryDirectory(prefix="pdm-phase5-preflight-") as td:
            os.environ["PDM_METROPT_ROOT"] = str(Path(td) / "missing-real-runtime")
            with TestClient(app) as client:
                status = client.get("/api/real-data/metropt/status")
                modes = client.get("/api/data/modes")
                operations = client.get("/operations")
                methods = client.get("/methodology")
    finally:
        if old_root is None:
            os.environ.pop("PDM_METROPT_ROOT", None)
        else:
            os.environ["PDM_METROPT_ROOT"] = old_root

    source_manifest = json.loads((ROOT / "data" / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    metropt_source = next((x for x in source_manifest.get("sources", []) if x.get("uci_id") == 791), None)
    raw_runtime = ROOT / "data" / "external" / "metropt3" / "raw"
    local_raw = [str(p.relative_to(ROOT)) for p in raw_runtime.rglob("*") if p.is_file()] if raw_runtime.exists() else []
    with tempfile.TemporaryDirectory(prefix="pdm-phase5-package-") as td:
        probe = Path(td) / "release.zip"
        package_repository(ROOT, probe)
        with zipfile.ZipFile(probe) as archive:
            bundled_raw = [name for name in archive.namelist() if "/data/external/" in name]
    mode = next(x for x in modes.json()["modes"] if x["id"] == "metropt3_real_operations")
    sensor_set = set(ANALOG_COLUMNS + DIGITAL_COLUMNS)
    checks = {
        "real_excerpt_parser": parsed.row_count == 6 and sensor_set.issubset(set(parsed.columns)),
        "real_excerpt_no_missing": parsed.missing_values == 0,
        "missing_runtime_refuses_readiness": status.status_code == 200 and status.json()["status"] == "NOT_READY",
        "no_synthetic_substitute_claim": "No synthetic substitute" in status.json()["claim_boundary"],
        "data_mode_not_ready_without_full_runtime": mode["status"] == "NOT_READY",
        "operations_ui_packaged": operations.status_code == 200 and "One real compressor" in operations.text,
        "methodology_separates_real_track": methods.status_code == 200 and "MetroPT-3 stays a separate evidence track" in methods.text,
        "source_manifest_attribution": bool(metropt_source and metropt_source.get("doi") == "10.24432/C5VW3R"),
        "raw_metropt_not_packaged": not bundled_raw,
        "full_acceptance_script_present": (ROOT / "scripts" / "phase5_acceptance.ps1").exists(),
    }
    report = {
        "phase": 5,
        "overall": "PASS" if all(checks.values()) else "FAIL",
        "evidence_class": "STRUCTURAL_PREFLIGHT_ONLY_NOT_FULL_REAL_DATA_VALIDATION",
        "checks": checks,
        "real_excerpt": parsed.to_dict(),
        "raw_metropt_files_packaged": bundled_raw,
        "local_metropt_raw_present": bool(local_raw),
        "claim_boundary": (
            "This preflight validates parser, packaging, readiness refusal and product wiring only. "
            "Full MetroPT-3 model evidence requires the complete UCI source on the Windows acceptance runtime."
        ),
    }
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts" / "phase5_preflight.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    if report["overall"] != "PASS":
        raise SystemExit(2)
    print("PHASE5_STRUCTURAL_PREFLIGHT_PASS")


if __name__ == "__main__":
    main()
