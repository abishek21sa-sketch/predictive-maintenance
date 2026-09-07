from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app, release_snapshot
from pdm_intelligence.planner.horizon import (
    build_intervention_horizon,
    compare_intervention_scenarios,
)


def main() -> int:
    assets = release_snapshot()["assets"]
    baseline = build_intervention_horizon(assets, solver="highs")
    scenarios = compare_intervention_scenarios(assets, solver="highs")
    client = TestClient(app)
    api_checks = {
        "health": client.get("/api/health").status_code,
        "planner": client.get("/api/planner/horizon").status_code,
        "scenarios": client.get("/api/planner/scenarios").status_code,
        "frontend": client.get("/").status_code,
    }
    schedule_ids = {x["asset_id"] for x in baseline["schedule"]}
    checks = {
        "planner_feasible": baseline["feasibility"]["feasible"],
        "all_assets_assigned_once": len(schedule_ids) == baseline["asset_count"] == len(baseline["schedule"]),
        "bay_capacity_respected": max(x["bays_used"] for x in baseline["resource_profile"]) <= baseline["assumptions"]["bay_capacity"],
        "labor_capacity_respected": max(x["labor_hours_used"] for x in baseline["resource_profile"]) <= baseline["assumptions"]["labor_hours_per_cycle"] + 1e-8,
        "scenario_sensitivity": scenarios["scenarios"]["baseline"]["objective"] != scenarios["scenarios"]["accelerated_degradation"]["objective"],
        "api_smoke": all(code == 200 for code in api_checks.values()),
    }
    payload = {
        "phase": 2,
        "overall": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "api": api_checks,
        "planner": {
            "asset_count": baseline["asset_count"],
            "solver": baseline["solver_evidence"]["solver"],
            "solver_status": baseline["solver_evidence"]["status"],
            "objective": baseline["solver_evidence"]["objective_value"],
            "mip_gap": baseline["solver_evidence"]["mip_gap"],
            "max_bays_used": max(x["bays_used"] for x in baseline["resource_profile"]),
            "max_labor_hours_used": max(x["labor_hours_used"] for x in baseline["resource_profile"]),
        },
        "evidence_class": "OPTIMIZATION_VERIFIED_ON_HISTORICAL_BENCHMARK_SNAPSHOT",
    }
    out = ROOT / "artifacts" / "phase2_diagnostics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
