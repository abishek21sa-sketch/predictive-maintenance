from __future__ import annotations

import json
import os
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pdm_intelligence.api.main import app
from pdm_intelligence.operations.command import (
    build_metropt_operations_case,
    commit_metropt_work_order,
    resource_scenario_from_name,
    update_work_order_status,
)
from pdm_intelligence.real_data.metropt import (
    METROPT_EXPECTED_ROWS,
    METROPT_FAILURE_EVENTS,
    METROPT_MODEL_QUALITY_GATE_NAME,
    load_metropt_runtime,
)


def main() -> None:
    root = Path(os.getenv("PDM_METROPT_ROOT", "data/external/metropt3"))
    runtime = load_metropt_runtime(root)
    if runtime is None:
        raise SystemExit("MetroPT-3 runtime evidence missing. Run scripts/prepare_metropt3.py --download first.")
    model = runtime["model"]
    raw = runtime["dataset"]["raw_validation"]
    runtime_integrity = runtime.get("integrity_check", {})
    model_ready = bool(model["quality_gate"].get("passed") is True and model["quality_gate"].get("promotion_allowed") is True)
    at = str(METROPT_FAILURE_EVENTS[-1].start_ts - pd.Timedelta(hours=3))
    with tempfile.TemporaryDirectory(prefix="pdm-phase5-") as td:
        state_db = Path(td) / "state.db"
        baseline = build_metropt_operations_case(root, state_db, at=at, scenario=resource_scenario_from_name("baseline", bays=2), solver="highs")
        disrupted = build_metropt_operations_case(root, state_db, at=at, scenario=resource_scenario_from_name("compound_disruption", bays=2), solver="highs")
        committed = commit_metropt_work_order(root, state_db, at=at, scenario=resource_scenario_from_name("baseline", bays=2), solver="highs")
        started = update_work_order_status(state_db, committed["work_order_id"], "IN_PROGRESS", actor="phase5_diagnostic")
        completed = update_work_order_status(state_db, committed["work_order_id"], "COMPLETED", actor="phase5_diagnostic")
    old_root = os.environ.get("PDM_METROPT_ROOT")
    old_db = os.environ.get("PDM_STATE_DB")
    try:
        os.environ["PDM_METROPT_ROOT"] = str(root)
        with tempfile.TemporaryDirectory(prefix="pdm-phase5-api-") as td:
            os.environ["PDM_STATE_DB"] = str(Path(td) / "api.db")
            with TestClient(app) as client:
                status_resp = client.get("/api/real-data/metropt/status")
                case_resp = client.get("/api/operations/metropt/case", params={"at": at, "scenario": "baseline", "bays": 2})
                page_resp = client.get("/operations")
    finally:
        if old_root is None: os.environ.pop("PDM_METROPT_ROOT", None)
        else: os.environ["PDM_METROPT_ROOT"] = old_root
        if old_db is None: os.environ.pop("PDM_STATE_DB", None)
        else: os.environ["PDM_STATE_DB"] = old_db
    checks = {
        "real_source_row_count": raw["row_count"] == METROPT_EXPECTED_ROWS,
        "real_source_no_missing": raw["missing_values"] == 0,
        "real_source_all_15_signals": all(name in raw["columns"] for name in ["TP2","TP3","H1","DV_pressure","Reservoirs","Oil_temperature","Motor_current","COMP","DV_eletric","Towers","MPG","LPS","Pressure_switch","Oil_level","Caudal_impulses"]),
        "chronological_no_shuffle": model["partition"]["shuffle"] is False,
        "holdout_is_future_period": model["partition"]["holdout_from"].startswith("2020-07-01"),
        "validation_model_quality_gate": model["quality_gate"]["validation_passed"],
        "holdout_model_quality_gate": model["quality_gate"]["holdout_passed"],
        "quality_gate_is_explicit": (
            model["quality_gate"]["name"] == METROPT_MODEL_QUALITY_GATE_NAME
            and model["quality_gate"]["passed"] is model_ready
            and model["quality_gate"]["promotion_allowed"] is model_ready
        ),
        "independent_event_coverage_is_explicit": (
            model["quality_gate"]["event_coverage"]["minimum_independent_events_per_evaluation"] >= 2
            and "validation" in model["quality_gate"]["event_coverage"]
            and "holdout" in model["quality_gate"]["event_coverage"]
        ),
        "anomaly_reference_february": model["anomaly"]["reference_period"].startswith("2020-02-01"),
        "published_failure_events_4": model["anomaly"]["event_count"] == 4,
        "runtime_artifacts_integrity": runtime_integrity.get("passed") is True,
        "operations_case_ready": baseline["status"] == "READY",
        "real_observed_boundary": baseline["asset"]["evidence_class"] == "REAL_OPERATIONAL_TELEMETRY",
        "no_fake_rul": "no certified rul" in baseline["predicted"]["uncertainty_note"].lower(),
        "optimization_optimal": baseline["optimized"]["solver_evidence"]["status"] == "OPTIMAL",
        "disruption_sensitivity": baseline["optimized"]["solver_evidence"]["objective_value"] != disrupted["optimized"]["solver_evidence"]["objective_value"],
        "work_order_closed_loop": (
            committed["status"] == "COMMITTED"
            and started["status"] == "IN_PROGRESS"
            and completed["status"] == "COMPLETED"
            and completed["payload"]["workflow_history"][-1]["evidence_class"] == "USER_RECORDED_WORK_ORDER_STATE"
        ),
        "api_real_status": (
            status_resp.status_code == 200
            and status_resp.json()["status"] == ("READY" if model_ready else "MODEL_GATE_BLOCKED")
            and status_resp.json()["source_status"] == "READY"
            and status_resp.json()["model_status"] == ("PROMOTION_READY" if model_ready else "PROMOTION_BLOCKED")
            and status_resp.json().get("runtime_integrity", {}).get("passed") is True
        ),
        "api_operations_case": (
            case_resp.status_code == 200
            and case_resp.json()["status"] == "READY"
            and case_resp.json()["operational_readiness"] == ("PROMOTION_READY" if model_ready else "HISTORICAL_ONLY_MODEL_GATE_BLOCKED")
        ),
        "operations_ui": page_resp.status_code == 200 and "Real Operations Command" in page_resp.text,
    }
    checks["quality_gate_fail_closed"] = bool(
        checks["quality_gate_is_explicit"]
        and (
            model_ready
            and checks["validation_model_quality_gate"]
            and checks["holdout_model_quality_gate"]
            or (
                not model_ready
                and model["quality_gate"].get("passed") is False
                and model["quality_gate"].get("promotion_allowed") is False
                and bool(model["quality_gate"].get("failure_reason"))
            )
        )
    )
    diagnostic_checks = {
        name: value
        for name, value in checks.items()
        if name not in {"validation_model_quality_gate", "holdout_model_quality_gate"}
    }
    report = {
        "phase": 5,
        "release": "real-operations-command",
        "overall": "PASS" if all(diagnostic_checks.values()) else "FAIL",
        "evidence_class": "REAL_OPERATIONAL_DATA_END_TO_END_VALIDATION",
        "runtime_evidence_sha256": sha256(
            (root / "derived" / "metropt_runtime.json").read_bytes()
        ).hexdigest(),
        "checks": checks,
        "raw_validation": raw,
        "model": {
            "selected_model": model["selected_model"],
            "complex_model_promoted": model["complex_model_promoted"],
            "holdout_metrics": model["holdout_metrics"],
            "quality_gate": model["quality_gate"],
            "partition": model["partition"],
            "baseline_validation": model["validation_metrics"]["logistic_baseline"],
            "candidate_validation": model["validation_metrics"]["random_forest"],
            "candidate_validation_metrics": model["validation_metrics"],
            "anomaly_quality": model["anomaly"].get("quality"),
            "official_event_detection": model["anomaly"]["official_event_detection"],
        },
        "operations": {
            "timestamp": baseline["asset"]["timestamp"],
            "failure_probability": baseline["predicted"]["failure_probability"],
            "anomaly_score": baseline["predicted"]["anomaly_score"],
            "target_choice": baseline["optimized"]["target_choice"],
            "baseline_objective": baseline["optimized"]["solver_evidence"]["objective_value"],
            "disrupted_objective": disrupted["optimized"]["solver_evidence"]["objective_value"],
        },
        "claim_boundary": runtime["claim_boundary"],
        "runtime_integrity": runtime_integrity,
    }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/phase5_diagnostics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["overall"] != "PASS":
        raise SystemExit(2)
    print("PHASE5_REAL_OPERATIONS_DIAGNOSTICS_PASS")


if __name__ == "__main__":
    main()
