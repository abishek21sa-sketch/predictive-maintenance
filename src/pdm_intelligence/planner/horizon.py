from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pdm_intelligence.optimization.maintenance import (
    AssetMaintenanceInput,
    solve_maintenance,
    validate_schedule,
)


def _risk_band(rul: float) -> str:
    if rul <= 15:
        return "critical"
    if rul <= 30:
        return "high"
    if rul <= 60:
        return "watch"
    return "stable"


def _planner_inputs(assets: list[dict[str, Any]], max_assets: int = 18) -> list[AssetMaintenanceInput]:
    ranked = sorted(
        assets,
        key=lambda x: (
            int(x.get("decision", {}).get("priority", 9)),
            float(x.get("predicted_rul", 1e9)),
            -float(x.get("anomaly_score", 0.0)),
        ),
    )[:max_assets]
    out: list[AssetMaintenanceInput] = []
    for row in ranked:
        rul = float(row["predicted_rul"])
        risk = float(row.get("failure_risk", 0.0))
        # Deterministic maintenance-resource assumptions for the benchmark planner.
        if rul <= 20:
            duration, labor, skill, part = 3, 24.0, "senior_mechanic", "hot_section_kit"
        elif rul <= 45:
            duration, labor, skill, part = 2, 14.0, "mechanic", "inspection_kit"
        else:
            duration, labor, skill, part = 1, 8.0, "mechanic", "inspection_kit"
        out.append(
            AssetMaintenanceInput(
                asset_id=int(row["unit_id"]),
                predicted_rul=rul,
                failure_probability=risk,
                duration=duration,
                labor_hours=labor,
                required_skill=skill,
                part_id=part,
                spare_units=1,
            )
        )
    return out


def build_intervention_horizon(
    assets: list[dict[str, Any]],
    *,
    horizon: int = 30,
    bay_capacity: int = 2,
    labor_hours_per_cycle: float = 24.0,
    scenario: str = "baseline",
    solver: str = "auto",
) -> dict[str, Any]:
    if scenario not in {"baseline", "accelerated_degradation", "conservative"}:
        raise ValueError("scenario must be baseline, accelerated_degradation, or conservative")
    inputs = _planner_inputs(assets)
    if not inputs:
        return {"scenario": scenario, "assets": [], "schedule": [], "resource_profile": []}

    factor = {"baseline": 1.0, "accelerated_degradation": 0.75, "conservative": 0.90}[scenario]
    adjusted = [
        AssetMaintenanceInput(**{**asdict(x), "predicted_rul": max(1.0, x.predicted_rul * factor)})
        for x in inputs
    ]
    skills = {"mechanic": max(1, bay_capacity), "senior_mechanic": max(1, bay_capacity)}
    inventory = {
        "inspection_kit": sum(x.spare_units for x in adjusted if x.part_id == "inspection_kit"),
        "hot_section_kit": sum(x.spare_units for x in adjusted if x.part_id == "hot_section_kit"),
    }
    result = solve_maintenance(
        adjusted,
        horizon=horizon,
        capacity_per_cycle=bay_capacity,
        labor_hours_per_cycle=labor_hours_per_cycle,
        skill_capacity_per_cycle=skills,
        part_inventory=inventory,
        solver=solver,
    )
    check = validate_schedule(
        result.schedule,
        adjusted,
        horizon,
        bay_capacity,
        labor_hours_per_cycle=labor_hours_per_cycle,
        skill_capacity_per_cycle=skills,
    )
    by_id = {int(a["unit_id"]): a for a in assets}
    by_input = {x.asset_id: x for x in adjusted}
    schedule = []
    for item in result.schedule:
        source = by_id[item.asset_id]
        inp = by_input[item.asset_id]
        schedule.append(
            {
                "asset_id": item.asset_id,
                "asset_label": f"ENG-{item.asset_id:03d}",
                "start_cycle": item.cycle,
                "end_cycle": item.cycle + item.duration - 1,
                "duration": item.duration,
                "predicted_rul": round(inp.predicted_rul, 2),
                "rul_low": round(max(0.0, inp.predicted_rul - 10.0), 2),
                "rul_high": round(inp.predicted_rul + 10.0, 2),
                "risk": round(inp.failure_probability, 4),
                "risk_band": _risk_band(inp.predicted_rul),
                "anomaly_score": round(float(source.get("anomaly_score", 0.0)), 4),
                "expected_cost": round(item.expected_cost, 2),
                "required_skill": item.required_skill,
                "part_id": item.part_id,
                "labor_hours": inp.labor_hours,
                "action": source.get("decision", {}).get("action", "plan_maintenance"),
                "rationale": source.get("decision", {}).get("rationale", ""),
            }
        )

    resource_profile = []
    for cycle in range(1, horizon + 1):
        active = [x for x in schedule if x["start_cycle"] <= cycle <= x["end_cycle"]]
        labor = sum(by_input[x["asset_id"]].labor_hours / by_input[x["asset_id"]].duration for x in active)
        resource_profile.append(
            {
                "cycle": cycle,
                "bays_used": len(active),
                "bay_capacity": bay_capacity,
                "labor_hours_used": round(labor, 2),
                "labor_hours_capacity": labor_hours_per_cycle,
            }
        )

    return {
        "scenario": scenario,
        "evidence_class": "OPTIMIZED_DECISION_ON_HISTORICAL_BENCHMARK_SNAPSHOT",
        "horizon": horizon,
        "asset_count": len(schedule),
        "schedule": sorted(schedule, key=lambda x: (x["start_cycle"], x["asset_id"])),
        "resource_profile": resource_profile,
        "feasibility": check,
        "solver_evidence": result.evidence.to_dict(),
        "assumptions": {
            "bay_capacity": bay_capacity,
            "labor_hours_per_cycle": labor_hours_per_cycle,
            "skills": skills,
            "parts": inventory,
            "rul_interval_note": "Illustrative +/-10 cycle planner band; model calibration remains separately documented.",
        },
    }


def compare_intervention_scenarios(assets: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    plans = {
        name: build_intervention_horizon(assets, scenario=name, **kwargs)
        for name in ("baseline", "conservative", "accelerated_degradation")
    }
    return {
        "scenarios": {
            name: {
                "objective": plan["solver_evidence"]["objective_value"],
                "solver": plan["solver_evidence"]["solver"],
                "status": plan["solver_evidence"]["status"],
                "max_bays_used": max((x["bays_used"] for x in plan["resource_profile"]), default=0),
                "max_labor_hours_used": max((x["labor_hours_used"] for x in plan["resource_profile"]), default=0),
            }
            for name, plan in plans.items()
        },
        "interpretation": "Scenario objectives are modeled expected costs, not realized savings.",
    }
