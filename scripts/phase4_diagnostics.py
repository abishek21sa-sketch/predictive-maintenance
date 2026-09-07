from __future__ import annotations

import json
from collections import Counter

from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app, release_snapshot
from pdm_intelligence.decision.intelligence import (
    build_decision_intelligence_plan,
    stress_test_decision_intelligence,
)
from pdm_intelligence.optimization.intervention import (
    InterventionAssumptions,
    default_capacity_profile,
    enumerate_intervention_small_instance,
    solve_intervention_portfolio,
)
from pdm_intelligence.optimization.maintenance import AssetMaintenanceInput


def main() -> None:
    tiny = [
        AssetMaintenanceInput(1, 8.0, 0.65, duration=2, labor_hours=8.0, required_skill="mechanic", part_id="kit"),
        AssetMaintenanceInput(2, 18.0, 0.35, duration=1, labor_hours=4.0, required_skill="mechanic", part_id="kit"),
    ]
    assumptions = {1: InterventionAssumptions(criticality_multiplier=1.6), 2: InterventionAssumptions(criticality_multiplier=1.1)}
    profile = default_capacity_profile(4, bays=1, labor_hours_per_cycle=8.0,
                                       skill_capacity={"mechanic": 1},
                                       part_inventory={"kit": 2, "inspection_kit": 2})
    scenarios = [{1: 5.0, 2: 15.0}, {1: 9.0, 2: 20.0}, {1: 12.0, 2: 25.0}]
    probs = [0.2, 0.5, 0.3]
    exact, _ = enumerate_intervention_small_instance(
        tiny, horizon=4, assumptions_by_asset=assumptions, rul_scenarios=scenarios,
        scenario_probabilities=probs, capacity_profiles=[profile], risk_aversion=0.25,
        cvar_alpha=0.80, critical_rul_threshold=10.0, max_deferrals=1,
    )
    milp = solve_intervention_portfolio(
        tiny, horizon=4, assumptions_by_asset=assumptions, rul_scenarios=scenarios,
        scenario_probabilities=probs, capacity_profiles=[profile], risk_aversion=0.25,
        cvar_alpha=0.80, critical_rul_threshold=10.0, max_deferrals=1, solver="highs",
    )
    oracle_match = abs(exact - milp.evidence.objective_value) < 1e-5

    assets = release_snapshot()["assets"]
    plan = build_decision_intelligence_plan(assets, solver="highs", n_rul_scenarios=9, seed=4401)
    action_counts = Counter(x["action"] for x in plan["choices"])
    stress_a = stress_test_decision_intelligence(
        assets, solver="highs", n_rul_scenarios=7, plan_seed=4401,
        n_simulations=160, simulation_seed=9917,
    )
    stress_b = stress_test_decision_intelligence(
        assets, solver="highs", n_rul_scenarios=7, plan_seed=4401,
        n_simulations=160, simulation_seed=9917,
    )
    reproducible = stress_a["stress_test"] == stress_b["stress_test"]
    policy_rows = {x["policy"]: x for x in stress_a["stress_test"]["results"]}

    with TestClient(app) as client:
        plan_api = client.get("/api/decision-intelligence/plan?n_rul_scenarios=5&bay_capacity=2")
        stress_api = client.get("/api/decision-intelligence/stress-test?n_rul_scenarios=5&n_simulations=40")
        page = client.get("/stress-lab")
        methods = client.get("/methodology")
        api_ok = plan_api.status_code == stress_api.status_code == page.status_code == methods.status_code == 200
        ui_ok = "Shock the plan" in page.text and "CVaR" in page.text and "common random numbers" in methods.text

    checks = {
        "cvar_milp_matches_exact_oracle": oracle_match,
        "uncertainty_aware_plan_optimal": plan["solver_evidence"]["status"] == "OPTIMAL",
        "cvar_not_below_expected": plan["risk_evidence"]["cvar_cost"] >= plan["risk_evidence"]["expected_cost"],
        "robust_capacity_profiles_present": set(plan["capacity_profiles"]) == {"baseline", "bay_and_crew_stress"},
        "critical_assets_not_silently_deferred": all(
            not (x["action"] == "defer" and next(a for a in assets if int(a["unit_id"]) == x["asset_id"])["predicted_rul"] <= 15)
            for x in plan["choices"]
        ),
        "multiple_intervention_types_selected": len(action_counts) >= 2,
        "stress_simulation_reproducible": reproducible,
        "common_random_numbers": stress_a["stress_test"]["common_random_numbers"] is True,
        "stress_policy_set_complete": set(policy_rows) == {"optimized_intervention", "earliest_rul_heuristic", "run_to_failure"},
        "phase4_api_smoke": api_ok,
        "stress_lab_and_methodology": ui_ok,
    }
    overall = all(checks.values())
    output = {
        "phase": 4,
        "version": "1.0.0",
        "overall": "PASS" if overall else "FAIL",
        "evidence_class": "MATHEMATICALLY_VERIFIED_OR_AND_SEEDED_STRESS_SIMULATION",
        "checks": checks,
        "oracle": {"enumeration_objective": exact, "milp_objective": milp.evidence.objective_value},
        "decision_plan": {
            "solver": plan["solver_evidence"],
            "risk": plan["risk_evidence"],
            "action_counts": dict(action_counts),
            "capacity_profiles": plan["capacity_profiles"],
        },
        "stress_results": stress_a["stress_test"]["results"],
        "claim_boundary": "Optimization and stress-test quantities are modeled/simulated outcomes, not realized field savings.",
    }
    print(json.dumps(output, indent=2))
    if not overall:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
