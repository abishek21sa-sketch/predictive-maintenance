from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from pdm_intelligence.data.cmapss import add_training_rul
from pdm_intelligence.data.synthetic import generate_cmapss_fixture
from pdm_intelligence.models.rul import train_rul_models
from pdm_intelligence.optimization.maintenance import (
    AssetMaintenanceInput,
    enumerate_small_instance,
    solve_maintenance,
    validate_schedule,
)
from pdm_intelligence.reliability.analysis import fit_reliability, weibull_hazard
from pdm_intelligence.reliability.economics import expected_maintenance_economics
from pdm_intelligence.simulation.lifecycle import compare_policies


def main() -> int:
    checks: dict[str, dict] = {}

    # AI diagnostic: group holdout synthetic benchmark against explicit age-only baseline.
    data = add_training_rul(generate_cmapss_fixture(n_units=24, min_cycles=55, max_cycles=90, seed=731))
    model = train_rul_models(data, validation_fraction=0.25, seed=731)
    checks["ai_rul"] = {
        "status": "PASS" if model.metrics["rmse"] < model.baseline_metrics["rmse"] else "FAIL",
        "evidence_class": "VALIDATED_ON_SYNTHETIC_BENCHMARK",
        "selected_model": model.selected_model,
        "rmse": model.metrics["rmse"],
        "baseline_rmse": model.baseline_metrics["rmse"],
        "r2": model.metrics["r2"],
        "residual_interval_90": model.residual_interval_90,
        "empirical_interval_coverage": model.interval_coverage_90,
    }

    # Reliability/IE mathematics diagnostic.
    rel = fit_reliability([100, 120, 140, 160], [10, 10, 10, 10])
    hz_scale = float(weibull_hazard([rel.weibull_scale], rel.weibull_shape, rel.weibull_scale)[0])
    availability_ok = math.isclose(rel.availability, rel.mtbf_cycles / (rel.mtbf_cycles + rel.mttr_cycles))
    hazard_ok = math.isclose(hz_scale, rel.weibull_shape / rel.weibull_scale, rel_tol=1e-9)
    econ = expected_maintenance_economics(0.25, downtime_cost_per_cycle=1000)
    checks["ie_reliability_math"] = {
        "status": "PASS" if availability_ok and hazard_ok else "FAIL",
        "evidence_class": "MATHEMATICALLY_VERIFIED",
        "availability": rel.availability,
        "weibull_shape": rel.weibull_shape,
        "weibull_scale": rel.weibull_scale,
        "economics_expected_total_cost": econ.expected_total_cost,
    }

    # OR diagnostic: duration-aware resource schedule + exact enumeration oracle.
    items = [
        AssetMaintenanceInput(1, 5, 0.80, duration=1),
        AssetMaintenanceInput(2, 7, 0.65, duration=1),
        AssetMaintenanceInput(3, 10, 0.45, duration=1),
    ]
    exact_obj, _ = enumerate_small_instance(items, horizon=3, capacity_per_cycle=1)
    plan = solve_maintenance(items, horizon=3, capacity_per_cycle=1, solver="highs")
    feasible = validate_schedule(plan.schedule, items, 3, 1)["feasible"]
    oracle_match = math.isclose(plan.objective_value, exact_obj, rel_tol=1e-9, abs_tol=1e-6)
    checks["or_schedule"] = {
        "status": "PASS" if feasible and oracle_match else "FAIL",
        "evidence_class": "OPTIMIZATION_VERIFIED_AGAINST_EXACT_ORACLE",
        "solver": plan.evidence.solver,
        "solver_status": plan.evidence.status,
        "objective": plan.objective_value,
        "enumeration_objective": exact_obj,
        "mip_gap": plan.evidence.mip_gap,
    }

    # Simulation reproducibility diagnostic.
    a = [x.to_dict() for x in compare_policies([12, 20, 35], n_simulations=120, seed=991)]
    b = [x.to_dict() for x in compare_policies([12, 20, 35], n_simulations=120, seed=991)]
    checks["simulation"] = {
        "status": "PASS" if a == b else "FAIL",
        "evidence_class": "SIMULATION_REPRODUCIBILITY_VERIFIED",
        "seed": 991,
        "results": a,
    }

    # Gurobi is the target primary solver on licensed user hardware; do not falsify availability here.
    has_gurobi = importlib.util.find_spec("gurobipy") is not None
    checks["gurobi_runtime"] = {
        "status": "AVAILABLE" if has_gurobi else "NOT_AVAILABLE_IN_BUILD_ENVIRONMENT",
        "evidence_class": "EXTERNAL_RUNTIME_CHECK",
        "required_for_phase1_build_validation": False,
        "required_for_final_primary_solver_acceptance": True,
    }

    overall = all(v["status"] == "PASS" for k, v in checks.items() if k != "gurobi_runtime")
    payload = {"phase": 1, "overall": "PASS" if overall else "FAIL", "checks": checks}
    out = Path("artifacts/phase1_diagnostics.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
