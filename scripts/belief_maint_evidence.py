from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdm_intelligence.fourx.signature_algorithm import (
    baseline_schedules,
    make_reference_problem,
    propagate_beliefs,
    solve_belief_maint,
    stress_transition_matrix,
)

OUT = ROOT / "artifacts" / "belief_maint"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    assets, transition, horizon, capacity = make_reference_problem()
    started = time.perf_counter()
    canonical = solve_belief_maint(assets, transition, horizon=horizon, crew_capacity_by_cycle=capacity)
    baselines = baseline_schedules(assets, transition, horizon=horizon, crew_capacity_by_cycle=capacity)
    histories = propagate_beliefs(assets, transition, horizon)
    stressed_transition = stress_transition_matrix(transition, 1.5)
    stressed_histories = propagate_beliefs(assets, stressed_transition, horizon)

    canonical_order = {x.asset_id: x.maintenance_cycle for x in canonical.schedule}
    risk_rank_order = baselines["risk_rank"]["maintenance_cycle_by_asset"]
    checks = {
        "canonical_optimal": canonical.status == "OPTIMAL",
        "crew_capacity_respected": len({x.maintenance_cycle for x in canonical.schedule}) == len(canonical.schedule),
        "beats_fixed_interval": canonical.objective_value <= baselines["fixed_interval"]["objective_value"] + 1e-6,
        "beats_risk_rank": canonical.objective_value < baselines["risk_rank"]["objective_value"] - 1e-6,
        "beats_lowest_load": canonical.objective_value <= baselines["lowest_production_load"]["objective_value"] + 1e-6,
        "belief_changes_risk_rank_timing": canonical_order != risk_rank_order,
        "stress_increases_failure_belief": bool(stressed_histories[103][-1, 3] > histories[103][-1, 3]),
    }

    rows = []
    for stress in (0.7, 1.0, 1.3, 1.6, 2.0):
        p = stress_transition_matrix(transition, stress)
        for crew in (1, 2):
            result = solve_belief_maint(assets, p, horizon=horizon, crew_capacity_by_cycle=crew)
            rows.append(
                {
                    "transition_stress": stress,
                    "crew_capacity": crew,
                    "status": result.status,
                    "objective_value": result.objective_value,
                    "first_asset": result.schedule[0].asset_id if result.schedule else None,
                    "first_cycle": result.schedule[0].maintenance_cycle if result.schedule else None,
                    "max_failure_probability_at_maintenance": max(
                        (x.failure_probability_at_maintenance for x in result.schedule), default=None
                    ),
                }
            )
    checks["sensitivity_grid_complete"] = len(rows) == 10 and all(r["status"] == "OPTIMAL" for r in rows)

    # Common-cause degradation Monte Carlo: every asset in a replication shares the
    # same sampled worsening factor, representing an environmental/process shock.
    # This is synthetic stress evidence, not a calibrated common-cause failure model.
    rng = np.random.default_rng(20260901)
    mc_rows = []
    for replication in range(200):
        common_factor = float(np.clip(rng.lognormal(mean=0.0, sigma=0.28), 0.55, 2.25))
        p = stress_transition_matrix(transition, common_factor)
        result = solve_belief_maint(assets, p, horizon=horizon, crew_capacity_by_cycle=capacity)
        mc_rows.append({
            "replication": replication,
            "seed": 20260901,
            "common_cause_transition_factor": common_factor,
            "status": result.status,
            "objective_value": result.objective_value,
            "first_asset": result.schedule[0].asset_id if result.schedule else None,
            "max_failure_probability_at_maintenance": max(
                (x.failure_probability_at_maintenance for x in result.schedule), default=None
            ),
        })
    checks["common_cause_monte_carlo_complete"] = len(mc_rows) == 200 and all(r["status"] == "OPTIMAL" for r in mc_rows)
    mc_objectives = np.asarray([float(r["objective_value"]) for r in mc_rows], dtype=float)
    checks["common_cause_monte_carlo_has_variation"] = float(np.ptp(mc_objectives)) > 1e-6

    payload = {
        "algorithm": "BELIEF-MAINT",
        "null_hypothesis": "Given common degradation beliefs, opportunity costs and crew capacity, BELIEF-MAINT does not reduce modeled cost relative to fixed-interval, risk-rank or lowest-load scheduling.",
        "runtime_seconds": time.perf_counter() - started,
        "evidence_class": "synthetic engineering optimization evidence",
        "checks": checks,
        "canonical": canonical.to_dict(),
        "baselines": baselines,
        "common_cause_monte_carlo": {
            "seed": 20260901,
            "replications": len(mc_rows),
            "objective_mean": float(mc_objectives.mean()),
            "objective_p95": float(np.quantile(mc_objectives, 0.95)),
            "objective_min": float(mc_objectives.min()),
            "objective_max": float(mc_objectives.max()),
            "interpretation": "Shared transition-severity shock applied to all assets per replication; synthetic stress evidence only.",
        },
        "claim_boundary": canonical.claim_boundary,
    }
    (OUT / "evidence.json").write_text(json.dumps(payload, indent=2, default=lambda x: x.item() if hasattr(x, "item") else str(x)), encoding="utf-8")
    with (OUT / "sensitivity.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    with (OUT / "common_cause_monte_carlo.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=mc_rows[0].keys())
        writer.writeheader(); writer.writerows(mc_rows)

    passed = sum(bool(v) for v in checks.values())
    report = [
        "# BELIEF-MAINT Evidence Report", "",
        f"Checks: **{passed}/{len(checks)} passed**.", "",
        f"- Canonical objective: {canonical.objective_value:.3f}",
        f"- Fixed-interval baseline: {baselines['fixed_interval']['objective_value']:.3f}",
        f"- Risk-rank baseline: {baselines['risk_rank']['objective_value']:.3f}",
        f"- Lowest-load baseline: {baselines['lowest_production_load']['objective_value']:.3f}",
        f"- Common-cause Monte Carlo: 200 replications, seed 20260901, P95 objective={float(np.quantile(mc_objectives, 0.95)):.3f}", "",
        "## Canonical schedule", "",
    ]
    report.extend(
        f"- Asset {x.asset_id}: cycle {x.maintenance_cycle}, P(failed)={x.failure_probability_at_maintenance:.4f}, modeled cost={x.modeled_cost:.2f}"
        for x in canonical.schedule
    )
    report.extend(["", "## Evidence boundary", "", canonical.claim_boundary])
    (OUT / "EVIDENCE_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print(f"BELIEF_MAINT_EVIDENCE={passed}/{len(checks)}")
    print(f"BELIEF_MAINT_SENSITIVITY={len(rows)}")
    print(f"BELIEF_MAINT_COMMON_CAUSE_MC={len(mc_rows)}")
    print(f"BELIEF_MAINT_OBJECTIVE={canonical.objective_value:.3f}")
    print(f"RISK_RANK_OBJECTIVE={baselines['risk_rank']['objective_value']:.3f}")
    print(f"FIXED_INTERVAL_OBJECTIVE={baselines['fixed_interval']['objective_value']:.3f}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
