from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app, release_snapshot
from pdm_intelligence.planner.horizon import (
    build_intervention_horizon,
    compare_intervention_scenarios,
)


def test_planner_schedule_is_feasible_and_resource_bounded():
    assets = release_snapshot()["assets"]
    plan = build_intervention_horizon(assets, horizon=30, bay_capacity=2, labor_hours_per_cycle=24.0, solver="highs")
    assert plan["asset_count"] > 0
    assert plan["feasibility"]["feasible"] is True
    assert plan["solver_evidence"]["status"] == "OPTIMAL"
    assert max(x["bays_used"] for x in plan["resource_profile"]) <= 2
    assert max(x["labor_hours_used"] for x in plan["resource_profile"]) <= 24.0 + 1e-8
    assert all(x["start_cycle"] <= x["end_cycle"] <= 30 for x in plan["schedule"])


def test_accelerated_degradation_changes_modeled_objective():
    assets = release_snapshot()["assets"]
    cmp = compare_intervention_scenarios(assets, horizon=30, bay_capacity=2, labor_hours_per_cycle=24.0, solver="highs")
    base = cmp["scenarios"]["baseline"]["objective"]
    accel = cmp["scenarios"]["accelerated_degradation"]["objective"]
    assert accel != base
    assert cmp["scenarios"]["baseline"]["status"] == "OPTIMAL"


def test_planner_api_contract():
    client = TestClient(app)
    response = client.get("/api/planner/horizon?scenario=baseline&bay_capacity=2&labor_hours_per_cycle=24")
    assert response.status_code == 200
    body = response.json()
    assert body["feasibility"]["feasible"] is True
    assert body["evidence_class"] == "OPTIMIZED_DECISION_ON_HISTORICAL_BENCHMARK_SNAPSHOT"
    assert body["solver_evidence"]["status"] in {"OPTIMAL", "TIME_LIMIT"}


def test_planner_rejects_unknown_scenario():
    client = TestClient(app)
    response = client.get("/api/planner/horizon?scenario=fictional")
    assert response.status_code == 422
