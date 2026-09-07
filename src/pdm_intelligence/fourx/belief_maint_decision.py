from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import numpy as np

from pdm_intelligence.fourx.signature_algorithm import (
    baseline_schedules,
    make_reference_problem,
    propagate_beliefs,
    solve_belief_maint,
    stress_transition_matrix,
)


def build_belief_maint_decision(
    *,
    transition_stress: float = 1.0,
    crew_capacity: int = 1,
    opportunity_cost_scale: float = 1.0,
) -> dict:
    if not 0.5 <= transition_stress <= 3.0:
        raise ValueError("transition_stress must lie in [0.5, 3.0]")
    if not 1 <= crew_capacity <= 5:
        raise ValueError("crew_capacity must lie in [1, 5]")
    if not 0.25 <= opportunity_cost_scale <= 3.0:
        raise ValueError("opportunity_cost_scale must lie in [0.25, 3.0]")

    assets, base_transition, horizon, _ = make_reference_problem()
    transition = stress_transition_matrix(base_transition, transition_stress)
    assets = [
        replace(
            asset,
            opportunity_cost_by_cycle=tuple(
                float(x) * opportunity_cost_scale for x in asset.opportunity_cost_by_cycle
            ),
        )
        for asset in assets
    ]
    capacity = tuple([crew_capacity] * horizon)
    result = solve_belief_maint(
        assets,
        transition,
        horizon=horizon,
        crew_capacity_by_cycle=capacity,
    )
    baselines = baseline_schedules(
        assets,
        transition,
        horizon=horizon,
        crew_capacity_by_cycle=capacity,
    )
    histories = propagate_beliefs(assets, transition, horizon)

    schedule = result.to_dict()["schedule"]
    cycle_counts: dict[int, int] = {}
    for row in schedule:
        cycle = int(row["maintenance_cycle"])
        cycle_counts[cycle] = cycle_counts.get(cycle, 0) + 1

    row_stochastic = np.allclose(np.asarray(transition).sum(axis=1), 1.0, atol=1e-10)
    capacity_ok = all(count <= crew_capacity for count in cycle_counts.values())
    optimizer_beats_baselines = all(
        result.objective_value <= float(payload["objective_value"]) + 1e-6
        for payload in baselines.values()
    )
    beliefs_valid = all(
        np.allclose(history.sum(axis=1), 1.0, atol=1e-10)
        and np.all(np.diff(history[:, 3]) >= -1e-12)
        for history in histories.values()
    )
    checks = {
        "solver_optimal": result.status == "OPTIMAL",
        "transition_rows_stochastic": bool(row_stochastic),
        "belief_histories_valid": bool(beliefs_valid),
        "crew_capacity_respected": bool(capacity_ok),
        "objective_not_worse_than_baselines": bool(optimizer_beats_baselines),
        "claim_boundary_present": "not a guaranteed" in result.claim_boundary,
    }
    authorized = all(checks.values())

    actions = []
    if authorized:
        for row in schedule:
            actions.append(
                {
                    "action": "SCHEDULE_MAINTENANCE_REVIEW",
                    "asset_id": int(row["asset_id"]),
                    "cycle": int(row["maintenance_cycle"]),
                    "failure_probability_at_maintenance": float(row["failure_probability_at_maintenance"]),
                    "modeled_cost": float(row["modeled_cost"]),
                }
            )

    belief_summary = []
    for asset in assets:
        history = histories[asset.asset_id]
        belief_summary.append(
            {
                "asset_id": asset.asset_id,
                "initial": {state: float(history[0, i]) for i, state in enumerate(("healthy", "degraded", "critical", "failed"))},
                "horizon_end": {state: float(history[-1, i]) for i, state in enumerate(("healthy", "degraded", "critical", "failed"))},
                "failed_probability_path": [float(x) for x in history[:, 3]],
            }
        )

    fingerprint = {
        "algorithm": "BELIEF-MAINT",
        "parameters": {
            "transition_stress": transition_stress,
            "crew_capacity": crew_capacity,
            "opportunity_cost_scale": opportunity_cost_scale,
        },
        "result": result.to_dict(),
        "checks": checks,
    }
    decision_id = "BELIEF-" + hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16].upper()

    return {
        "decision_id": decision_id,
        "algorithm": "BELIEF-MAINT",
        "gate": "AUTHORIZED" if authorized else "BLOCKED",
        "human_review_required": True,
        "evidence_class": "synthetic Markov/MILP formulation validation",
        "parameters": fingerprint["parameters"],
        "checks": checks,
        "result": result.to_dict(),
        "baselines": baselines,
        "belief_summary": belief_summary,
        "actions": actions,
        "operator_note": (
            "AUTHORIZED means the modeled belief-state schedule passed formulation and evidence checks. "
            "It does not autonomously create work orders or override maintenance engineering review."
        ),
    }
