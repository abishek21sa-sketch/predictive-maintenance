from __future__ import annotations

import math
from typing import Any

from pdm_intelligence.planner.horizon import build_intervention_horizon
from pdm_intelligence.reliability.economics import expected_maintenance_economics


def _band(rul: float) -> str:
    if rul <= 15: return "critical"
    if rul <= 30: return "high"
    if rul <= 60: return "watch"
    return "stable"


def fleet_casebook(assets: list[dict[str, Any]], limit: int = 24) -> list[dict[str, Any]]:
    ranked = sorted(assets, key=lambda x: (int(x.get("decision", {}).get("priority", 9)), float(x["predicted_rul"])))[:limit]
    return [{
        "asset_id": int(x["unit_id"]),
        "asset_label": f"ENG-{int(x['unit_id']):03d}",
        "cycle": int(x.get("cycle", 0)),
        "predicted_rul": round(float(x["predicted_rul"]), 2),
        "failure_risk": round(float(x.get("failure_risk", 0.0)), 4),
        "anomaly_score": round(float(x.get("anomaly_score", 0.0)), 4),
        "risk_band": _band(float(x["predicted_rul"])),
        "action": x.get("decision", {}).get("action", "monitor"),
        "priority": int(x.get("decision", {}).get("priority", 9)),
        "confidence": round(float(x.get("decision", {}).get("confidence", 0.0)), 4),
    } for x in ranked]


def asset_dossier(assets: list[dict[str, Any]], asset_id: int) -> dict[str, Any]:
    source = next((x for x in assets if int(x["unit_id"]) == asset_id), None)
    if source is None:
        raise ValueError(f"asset {asset_id} not found")
    rul = float(source["predicted_rul"]); risk = float(source.get("failure_risk", 0.0)); anomaly = float(source.get("anomaly_score", 0.0)); age = int(source.get("cycle", 0))
    # Derived diagnostic trace: a deterministic visualization of the current prognostic state,
    # not reconstructed sensor history. It is explicitly labeled as derived in the API/UI.
    points = []
    for i in range(12):
        rel = i / 11
        health = max(0.02, min(1.0, 1.0 - rel * (0.35 + 0.45 * anomaly) - 0.12 * risk * rel * rel))
        points.append({"offset": i - 11, "health_index": round(health, 4)})
    horizon = max(5, min(80, math.ceil(rul * 1.35)))
    prognosis = []
    for t in range(0, horizon + 1, max(1, horizon // 16)):
        survival = math.exp(-((t / max(rul, 1.0)) ** 2.1))
        prognosis.append({"cycles_ahead": t, "survival_proxy": round(survival, 4)})
    return {
        "asset_id": asset_id,
        "asset_label": f"ENG-{asset_id:03d}",
        "current_cycle": age,
        "predicted_rul": round(rul, 2),
        "rul_interval": [round(max(0.0, rul - 10.0), 2), round(rul + 10.0, 2)],
        "failure_risk": round(risk, 4),
        "anomaly_score": round(anomaly, 4),
        "risk_band": _band(rul),
        "recommended_action": source.get("decision", {}).get("action", "monitor"),
        "confidence": round(float(source.get("decision", {}).get("confidence", 0.0)), 4),
        "rationale": source.get("decision", {}).get("rationale", ""),
        "diagnostic_trace": points,
        "diagnostic_trace_class": "DERIVED_DIAGNOSTIC_VISUALIZATION_NOT_OBSERVED_SENSOR_HISTORY",
        "survival_trace": prognosis,
        "survival_trace_class": "MODELED_PROGNOSTIC_VISUALIZATION",
    }


def intervention_options(assets: list[dict[str, Any]], asset_id: int) -> dict[str, Any]:
    dossier = asset_dossier(assets, asset_id)
    p = dossier["failure_risk"]
    rul = dossier["predicted_rul"]
    maintain = expected_maintenance_economics(p).to_dict()
    defer_risk = min(1.0, p + max(0.08, 0.35 * (1.0 - min(rul, 60.0) / 60.0)))
    defer = expected_maintenance_economics(defer_risk, preventive_cost=5000.0).to_dict()
    inspect_risk = max(0.0, p * 0.75)
    inspect = expected_maintenance_economics(inspect_risk, preventive_cost=2200.0, preventive_downtime_cycles=0.5).to_dict()
    options = [
        {"id": "maintain_now", "label": "Maintain now", "timing": "next feasible slot", "risk_used": p, "economics": maintain, "interpretation": "Commit preventive work at the earliest resource-feasible window."},
        {"id": "inspect_first", "label": "Inspect first", "timing": "within 2 cycles", "risk_used": inspect_risk, "economics": inspect, "interpretation": "Buy diagnostic information before committing major maintenance."},
        {"id": "defer_5", "label": "Defer 5 cycles", "timing": "+5 cycles", "risk_used": defer_risk, "economics": defer, "interpretation": "Preserve near-term capacity while accepting greater modeled failure exposure."},
    ]
    return {"asset_id": asset_id, "evidence_class": "MODELED_INTERVENTION_ALTERNATIVES", "options": options}


def commitment_ledger(assets: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    plan = build_intervention_horizon(assets, **kwargs)
    return {
        "evidence_class": plan["evidence_class"],
        "solver_evidence": plan["solver_evidence"],
        "feasibility": plan["feasibility"],
        "commitments": plan["schedule"],
        "resource_profile": plan["resource_profile"],
        "assumptions": plan["assumptions"],
    }
