from __future__ import annotations

import math

from pdm_intelligence.domain import Action, MaintenanceDecision


def failure_risk_from_rul(rul_cycles: float, horizon: float = 30.0) -> float:
    # Monotonic calibrated heuristic used by the decision layer; model probability calibration is a later extension.
    return float(1.0 / (1.0 + math.exp((rul_cycles - horizon) / 7.5)))


def decide(asset_id: int, cycle: int, rul_cycles: float, anomaly_score: float, scheduled_cycle: int | None = None) -> MaintenanceDecision:
    risk = failure_risk_from_rul(rul_cycles)
    if rul_cycles <= 10 or risk >= 0.90:
        action, priority = Action.MAINTAIN_NOW, 1
    elif rul_cycles <= 30 or risk >= 0.55:
        action, priority = Action.PLAN_MAINTENANCE, 2
    elif anomaly_score >= 0.70:
        action, priority = Action.INSPECT, 3
    else:
        action, priority = Action.MONITOR, 4
    rec_cycle = int(cycle + (scheduled_cycle if scheduled_cycle is not None else max(1, min(30, rul_cycles * 0.6))))
    expected_cost = 5000.0 + risk * 30000.0
    confidence = max(0.50, min(0.98, 0.55 + 0.35 * abs(risk - 0.5) * 2 + 0.10 * anomaly_score))
    rationale = (
        f"Predicted RUL={rul_cycles:.1f} cycles, failure-risk={risk:.2f}, "
        f"anomaly={anomaly_score:.2f}; action selected by risk/RUL policy and capacity-aware schedule."
    )
    return MaintenanceDecision(asset_id, action, priority, rec_cycle, expected_cost, rationale, confidence)
