from __future__ import annotations

import math

from pdm_intelligence.api.main import release_snapshot
from pdm_intelligence.decision.intelligence import (
    build_decision_intelligence_plan,
    generate_rul_scenarios,
    stress_test_decision_intelligence,
)
from pdm_intelligence.optimization.intervention import (
    InterventionAssumptions,
    default_capacity_profile,
    enumerate_intervention_small_instance,
    failure_exposure_probability,
    precheck_intervention_feasibility,
    solve_intervention_portfolio,
)
from pdm_intelligence.optimization.maintenance import AssetMaintenanceInput


def test_failure_exposure_is_bounded_and_monotone_with_delay():
    p0 = failure_exposure_probability(0.20, 20.0, 0.0)
    p5 = failure_exposure_probability(0.20, 20.0, 5.0)
    p10 = failure_exposure_probability(0.20, 20.0, 10.0)
    assert math.isclose(p0, 0.20, abs_tol=1e-12)
    assert 0.20 < p5 < p10 < 1.0


def test_cvar_intervention_milp_matches_exact_enumeration_on_tiny_instance():
    items = [
        AssetMaintenanceInput(1, 8.0, 0.65, duration=2, labor_hours=8.0, required_skill="mechanic", part_id="kit"),
        AssetMaintenanceInput(2, 18.0, 0.35, duration=1, labor_hours=4.0, required_skill="mechanic", part_id="kit"),
    ]
    assumptions = {
        1: InterventionAssumptions(criticality_multiplier=1.6),
        2: InterventionAssumptions(criticality_multiplier=1.1),
    }
    profile = default_capacity_profile(4, bays=1, labor_hours_per_cycle=8.0,
                                       skill_capacity={"mechanic": 1},
                                       part_inventory={"kit": 2, "inspection_kit": 2})
    scenarios = [{1: 5.0, 2: 15.0}, {1: 9.0, 2: 20.0}, {1: 12.0, 2: 25.0}]
    probs = [0.2, 0.5, 0.3]
    exact, _ = enumerate_intervention_small_instance(
        items, horizon=4, assumptions_by_asset=assumptions,
        rul_scenarios=scenarios, scenario_probabilities=probs,
        capacity_profiles=[profile], risk_aversion=0.25, cvar_alpha=0.80,
        allow_defer=True, critical_rul_threshold=10.0, max_deferrals=1,
    )
    result = solve_intervention_portfolio(
        items, horizon=4, assumptions_by_asset=assumptions,
        rul_scenarios=scenarios, scenario_probabilities=probs,
        capacity_profiles=[profile], risk_aversion=0.25, cvar_alpha=0.80,
        allow_defer=True, critical_rul_threshold=10.0, max_deferrals=1,
        solver="highs",
    )
    assert result.evidence.status == "OPTIMAL"
    assert abs(result.evidence.objective_value - exact) < 1e-5
    assert abs(result.risk.objective_value - exact) < 1e-5
    assert len(result.choices) == 2


def test_critical_asset_cannot_be_deferred():
    item = AssetMaintenanceInput(1, 6.0, 0.8, duration=1, labor_hours=4.0, required_skill="mechanic", part_id="kit")
    profile = default_capacity_profile(5, bays=1, labor_hours_per_cycle=8.0,
                                       skill_capacity={"mechanic": 1}, part_inventory={"kit": 1, "inspection_kit": 1})
    result = solve_intervention_portfolio([item], horizon=5, capacity_profiles=[profile],
                                         critical_rul_threshold=10.0, solver="highs")
    assert result.choices[0].action in {"maintain", "inspect"}


def test_precheck_detects_impossible_mandatory_resource_profile():
    item = AssetMaintenanceInput(1, 5.0, 0.9, duration=2, labor_hours=8.0, required_skill="senior", part_id="kit")
    profile = default_capacity_profile(4, bays=1, labor_hours_per_cycle=1.0,
                                       skill_capacity={"senior": 0}, part_inventory={"kit": 0, "inspection_kit": 0})
    diag = precheck_intervention_feasibility([item], [profile], 4, allow_defer=True, critical_rul_threshold=10.0)
    assert diag.feasible_by_precheck is False
    assert any("asset_1" in x for x in diag.reasons)


def test_robust_plan_is_feasible_across_named_capacity_profiles():
    assets = release_snapshot()["assets"]
    plan = build_decision_intelligence_plan(assets, solver="highs", n_rul_scenarios=5, seed=4)
    assert plan["precheck"]["feasible_by_precheck"] is True
    assert plan["solver_evidence"]["status"] == "OPTIMAL"
    assert set(plan["capacity_profiles"]) == {"baseline", "bay_and_crew_stress"}
    assert plan["risk_evidence"]["cvar_cost"] >= plan["risk_evidence"]["expected_cost"]
    assert len(plan["choices"]) == plan["asset_count"]
    assert all(x["action"] in {"maintain", "inspect", "defer"} for x in plan["choices"])


def test_rul_scenario_generation_is_seed_reproducible():
    items = [AssetMaintenanceInput(i, 10.0 + i, 0.3) for i in range(1, 4)]
    a, pa = generate_rul_scenarios(items, n_scenarios=5, seed=77)
    b, pb = generate_rul_scenarios(items, n_scenarios=5, seed=77)
    assert a == b and pa == pb


def test_stress_simulation_uses_common_random_numbers_and_is_reproducible():
    assets = release_snapshot()["assets"]
    plan = build_decision_intelligence_plan(assets, solver="highs", n_rul_scenarios=5, seed=10)
    # Reconstruct the same planner inputs through the public stress function, which also validates one choice per asset.
    a = stress_test_decision_intelligence(assets, solver="highs", n_rul_scenarios=5,
                                          plan_seed=10, n_simulations=80, simulation_seed=101)
    b = stress_test_decision_intelligence(assets, solver="highs", n_rul_scenarios=5,
                                          plan_seed=10, n_simulations=80, simulation_seed=101)
    assert a["stress_test"] == b["stress_test"]
    assert a["stress_test"]["common_random_numbers"] is True
    policies = {x["policy"] for x in a["stress_test"]["results"]}
    assert policies == {"optimized_intervention", "earliest_rul_heuristic", "run_to_failure"}
    assert plan["solver_evidence"]["status"] == "OPTIMAL"


def test_phase4_api_and_stress_lab_contract():
    from fastapi.testclient import TestClient

    from pdm_intelligence.api.main import app
    with TestClient(app) as client:
        p = client.get('/api/decision-intelligence/plan?n_rul_scenarios=5&bay_capacity=2')
        assert p.status_code == 200, p.text
        body = p.json()
        assert body['evidence_class'] == 'UNCERTAINTY_AWARE_OPTIMIZED_DECISION'
        assert body['solver_evidence']['status'] in {'OPTIMAL', 'TIME_LIMIT'}
        assert body['risk_evidence']['cvar_cost'] >= body['risk_evidence']['expected_cost']
        s = client.get('/api/decision-intelligence/stress-test?n_rul_scenarios=5&n_simulations=40')
        assert s.status_code == 200, s.text
        assert s.json()['stress_test']['common_random_numbers'] is True
        page = client.get('/stress-lab')
        assert page.status_code == 200
        assert 'Shock the plan' in page.text
        assert 'CVaR' in page.text


def test_methodology_documents_uncertainty_aware_or_and_stress_simulation():
    from fastapi.testclient import TestClient

    from pdm_intelligence.api.main import app
    with TestClient(app) as client:
        page = client.get('/methodology')
        assert page.status_code == 200
        assert 'CVaR' in page.text
        assert 'maintain, inspect, or defer' in page.text
        assert 'common random numbers' in page.text
