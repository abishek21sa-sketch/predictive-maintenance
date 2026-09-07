from __future__ import annotations

from typing import Any

import numpy as np

from pdm_intelligence.optimization.intervention import (
    CapacityProfile,
    InterventionAssumptions,
    default_capacity_profile,
    precheck_intervention_feasibility,
    solve_intervention_portfolio,
)
from pdm_intelligence.optimization.maintenance import AssetMaintenanceInput
from pdm_intelligence.simulation.stress import StressConfig, compare_plan_under_stress


def _items_from_assets(assets: list[dict[str, Any]], max_assets: int = 18) -> tuple[list[AssetMaintenanceInput], dict[int, InterventionAssumptions]]:
    ranked = sorted(assets, key=lambda x: (int(x.get("decision", {}).get("priority", 9)), float(x["predicted_rul"])))[:max_assets]
    items = []
    assumptions = {}
    for row in ranked:
        rul = float(row["predicted_rul"]); risk = float(row.get("failure_risk", 0.0))
        if rul <= 20:
            duration, labor, skill, part, criticality = 3, 24.0, "senior_mechanic", "hot_section_kit", 1.6
        elif rul <= 45:
            duration, labor, skill, part, criticality = 2, 14.0, "mechanic", "inspection_kit", 1.25
        else:
            duration, labor, skill, part, criticality = 1, 8.0, "mechanic", "inspection_kit", 1.0
        asset_id = int(row["unit_id"])
        items.append(AssetMaintenanceInput(asset_id, rul, risk, duration=duration, labor_hours=labor,
                                           required_skill=skill, part_id=part, spare_units=1))
        assumptions[asset_id] = InterventionAssumptions(criticality_multiplier=criticality)
    return items, assumptions


def generate_rul_scenarios(items: list[AssetMaintenanceInput], *, n_scenarios: int = 9, seed: int = 4401,
                           sigma_fraction: float = 0.20) -> tuple[list[dict[int, float]], list[float]]:
    if n_scenarios < 3:
        raise ValueError("n_scenarios must be at least 3")
    rng = np.random.default_rng(seed)
    scenarios = []
    for _ in range(n_scenarios):
        scenarios.append({x.asset_id: max(1.0, float(rng.normal(x.predicted_rul, max(2.0, sigma_fraction * x.predicted_rul)))) for x in items})
    return scenarios, [1.0 / n_scenarios] * n_scenarios


def robust_capacity_profiles(items: list[AssetMaintenanceInput], horizon: int, bay_capacity: int,
                             labor_hours_per_cycle: float) -> list[CapacityProfile]:
    parts = {
        "hot_section_kit": sum(x.spare_units for x in items if x.part_id == "hot_section_kit"),
        "inspection_kit": sum(x.spare_units for x in items if x.part_id == "inspection_kit") + len(items),
    }
    baseline = default_capacity_profile(horizon, name="baseline", bays=bay_capacity,
                                       labor_hours_per_cycle=labor_hours_per_cycle,
                                       skill_capacity={"mechanic": max(1, bay_capacity), "senior_mechanic": max(1, bay_capacity)},
                                       part_inventory=parts)
    # Discrete uncertainty set: one-bay outage during cycles 8-12 and one senior-mechanic absence during 10-14.
    bays = list(baseline.bay_capacity)
    for t in range(7, min(12, horizon)):
        bays[t] = max(1, bays[t] - 1)
    skills = {k: list(v) for k, v in baseline.skill_capacity.items()}
    for t in range(9, min(14, horizon)):
        skills["senior_mechanic"][t] = max(1, skills["senior_mechanic"][t] - 1)
    stress = CapacityProfile("bay_and_crew_stress", tuple(bays), baseline.labor_hours,
                             {k: tuple(v) for k, v in skills.items()}, dict(parts))
    return [baseline, stress]


def build_decision_intelligence_plan(
    assets: list[dict[str, Any]], *, horizon: int = 30, bay_capacity: int = 2,
    labor_hours_per_cycle: float = 24.0, risk_aversion: float = 0.15,
    cvar_alpha: float = 0.90, n_rul_scenarios: int = 9, seed: int = 4401,
    solver: str = "auto",
) -> dict[str, Any]:
    items, assumptions = _items_from_assets(assets)
    scenarios, probs = generate_rul_scenarios(items, n_scenarios=n_rul_scenarios, seed=seed)
    profiles = robust_capacity_profiles(items, horizon, bay_capacity, labor_hours_per_cycle)
    precheck = precheck_intervention_feasibility(items, profiles, horizon, assumptions_by_asset=assumptions,
                                                 allow_defer=True, critical_rul_threshold=15.0)
    result = solve_intervention_portfolio(items, horizon=horizon, assumptions_by_asset=assumptions,
                                         rul_scenarios=scenarios, scenario_probabilities=probs,
                                         capacity_profiles=profiles, risk_aversion=risk_aversion,
                                         cvar_alpha=cvar_alpha, allow_defer=True, critical_rul_threshold=15.0,
                                         max_deferrals=max(1, len(items)//3), solver=solver)
    return {
        "evidence_class": "UNCERTAINTY_AWARE_OPTIMIZED_DECISION",
        "decision_problem": "choose maintain/inspect/defer and timing under prognostic and resource uncertainty",
        "asset_count": len(items),
        "seed": seed,
        "rul_scenario_count": len(scenarios),
        "precheck": precheck.to_dict(),
        **result.to_dict(),
        "claim_boundary": "Expected/CVaR costs are modeled decision quantities, not realized savings.",
    }


def stress_test_decision_intelligence(
    assets: list[dict[str, Any]], *, horizon: int = 30, bay_capacity: int = 2,
    labor_hours_per_cycle: float = 24.0, risk_aversion: float = 0.15,
    cvar_alpha: float = 0.90, n_rul_scenarios: int = 9, plan_seed: int = 4401,
    n_simulations: int = 500, simulation_seed: int = 9917, solver: str = "auto",
    stress_config: StressConfig | None = None,
) -> dict[str, Any]:
    items, assumptions = _items_from_assets(assets)
    scenarios, probs = generate_rul_scenarios(items, n_scenarios=n_rul_scenarios, seed=plan_seed)
    profiles = robust_capacity_profiles(items, horizon, bay_capacity, labor_hours_per_cycle)
    result = solve_intervention_portfolio(items, horizon=horizon, assumptions_by_asset=assumptions,
                                         rul_scenarios=scenarios, scenario_probabilities=probs,
                                         capacity_profiles=profiles, risk_aversion=risk_aversion,
                                         cvar_alpha=cvar_alpha, allow_defer=True, critical_rul_threshold=15.0,
                                         max_deferrals=max(1, len(items)//3), solver=solver)
    simulation = compare_plan_under_stress(items, result.choices, n_simulations=n_simulations,
                                           seed=simulation_seed, config=stress_config,
                                           heuristic_capacity=bay_capacity)
    return {
        "plan": result.to_dict(),
        "stress_test": simulation,
        "interpretation": "The optimizer is evaluated against the same random stress draws as the heuristic and run-to-failure policies.",
    }
