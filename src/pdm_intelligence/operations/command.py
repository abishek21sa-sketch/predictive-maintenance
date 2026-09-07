from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from pdm_intelligence.optimization.intervention import (
    CapacityProfile,
    InterventionAssumptions,
    solve_intervention_portfolio,
)
from pdm_intelligence.optimization.maintenance import AssetMaintenanceInput
from pdm_intelligence.real_data.metropt import (
    METROPT_DATASET_ID,
    METROPT_FAILURE_EVENTS,
    load_metropt_feature_store,
    load_metropt_runtime,
    score_metropt_frame,
)
from pdm_intelligence.storage.database import connect_state, ensure_schema_mutation_allowed

TARGET_ASSET_ID = 7001
TARGET_ASSET_NAME = "METROPT-3 / APU-01"


class ModelPromotionBlockedError(RuntimeError):
    """Raised when a production workflow attempts to use an unpromoted model."""


WORK_ORDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_order_id TEXT NOT NULL UNIQUE,
  asset_id TEXT NOT NULL,
  source_dataset TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  scheduled_cycle INTEGER,
  duration_cycles INTEGER NOT NULL,
  required_skill TEXT,
  part_id TEXT,
  evidence_class TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass(frozen=True)
class ResourceScenario:
    name: str
    bays: int = 2
    mechanics: int = 2
    electrical_techs: int = 1
    inspection_kits: int = 3
    service_kits: int = 2
    bay_outage: bool = False
    technician_absent: bool = False
    part_delay: bool = False
    duration_multiplier: float = 1.0


def resource_scenario_from_name(name: str, *, bays: int = 2) -> ResourceScenario:
    key = (name or "baseline").strip().lower()
    base = {"name": key, "bays": max(1, int(bays))}
    if key == "baseline":
        return ResourceScenario(**base)
    if key == "bay_outage":
        return ResourceScenario(**base, bay_outage=True)
    if key == "technician_absent":
        return ResourceScenario(**base, technician_absent=True)
    if key == "part_delay":
        return ResourceScenario(**base, part_delay=True)
    if key == "duration_plus_50":
        return ResourceScenario(**base, duration_multiplier=1.5)
    if key == "compound_disruption":
        return ResourceScenario(**base, bay_outage=True, technician_absent=True, duration_multiplier=1.5)
    raise ValueError(f"Unknown resource scenario {name}")


def _ensure_work_orders(db_path: str | Path) -> None:
    if not ensure_schema_mutation_allowed(db_path):
        return
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_state(path)) as con:
        con.executescript(WORK_ORDER_SCHEMA)
        con.commit()


def list_work_orders(db_path: str | Path) -> list[dict[str, Any]]:
    _ensure_work_orders(db_path)
    with closing(connect_state(db_path)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM work_orders ORDER BY id DESC").fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        out.append(item)
    return out


WORK_ORDER_TRANSITIONS = {
    "COMMITTED": {"IN_PROGRESS", "CANCELLED"},
    "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}


def update_work_order_status(
    db_path: str | Path,
    work_order_id: str,
    status: str,
    *,
    actor: str = "local_operator",
    note: str | None = None,
) -> dict[str, Any]:
    """Advance a committed decision through an operator-recorded work-order lifecycle.

    These states are application records, not claims that maintenance occurred in MetroPT-3.
    """
    target = status.strip().upper()
    if target not in WORK_ORDER_TRANSITIONS:
        raise ValueError(f"Unsupported work-order status {status}")
    _ensure_work_orders(db_path)
    with closing(connect_state(db_path)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM work_orders WHERE work_order_id=?", (work_order_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown work order {work_order_id}")
        current = str(row["status"]).upper()
        if target == current:
            pass
        elif target not in WORK_ORDER_TRANSITIONS.get(current, set()):
            raise ValueError(f"Invalid work-order transition {current} -> {target}")
        payload = json.loads(row["payload"])
        history = list(payload.get("workflow_history", []))
        history.append({
            "from": current,
            "to": target,
            "actor": actor,
            "note": note or "",
            "evidence_class": "USER_RECORDED_WORK_ORDER_STATE",
        })
        payload["workflow_history"] = history
        payload["workflow_claim_boundary"] = (
            "Work-order status is operator-recorded application state; it is not evidence "
            "that maintenance was historically performed in the MetroPT-3 source dataset."
        )
        con.execute(
            "UPDATE work_orders SET status=?, payload=?, updated_at=CURRENT_TIMESTAMP WHERE work_order_id=?",
            (target, json.dumps(payload, sort_keys=True), work_order_id),
        )
        con.commit()
        updated = con.execute(
            "SELECT * FROM work_orders WHERE work_order_id=?", (work_order_id,)
        ).fetchone()
    item = dict(updated)
    item["payload"] = json.loads(item["payload"])
    return item


def _event_context(timestamp: pd.Timestamp) -> dict[str, Any]:
    nearest = min(
        METROPT_FAILURE_EVENTS,
        key=lambda e: abs((e.start_ts - timestamp).total_seconds()),
    )
    hours = (nearest.start_ts - timestamp).total_seconds() / 3600.0
    return {
        "nearest_event_id": nearest.event_id,
        "nearest_event_start": str(nearest.start_ts),
        "hours_to_nearest_reported_failure_start": float(hours),
        "within_24h_prefailure_window": bool(0 <= hours <= 24),
        "within_reported_failure_window": bool(nearest.start_ts <= timestamp <= nearest.end_ts),
        "evidence_class": "OBSERVED_EVENT_TIMING_FROM_UCI_REPORT",
    }


def _select_timestamp(frame: pd.DataFrame, requested: str | None) -> pd.Timestamp:
    if requested:
        ts = pd.Timestamp(requested)
    else:
        # Default to three hours before the final published failure so the UI opens on a
        # real, decision-relevant historical case instead of an uneventful end-of-dataset row.
        ts = METROPT_FAILURE_EVENTS[-1].start_ts - pd.Timedelta(hours=3)
    available = frame[frame["timestamp"] <= ts]
    if available.empty:
        raise ValueError("Requested MetroPT timestamp precedes the available feature store")
    return pd.Timestamp(available.iloc[-1]["timestamp"])


def _resource_profiles(horizon: int, scenario: ResourceScenario) -> list[CapacityProfile]:
    bays = max(1, int(scenario.bays) - (1 if scenario.bay_outage else 0))
    mechanics = max(1, int(scenario.mechanics) - (1 if scenario.technician_absent else 0))
    electrical = max(1, int(scenario.electrical_techs) - (1 if scenario.technician_absent else 0))
    inspection_kits = 0 if scenario.part_delay else int(scenario.inspection_kits)
    service_kits = 0 if scenario.part_delay else int(scenario.service_kits)
    baseline = CapacityProfile(
        name=scenario.name,
        bay_capacity=tuple(bays for _ in range(horizon)),
        labor_hours=tuple(float(8 * (mechanics + electrical)) for _ in range(horizon)),
        skill_capacity={
            "mechanic": tuple(mechanics for _ in range(horizon)),
            "electrical_tech": tuple(electrical for _ in range(horizon)),
        },
        part_inventory={"apu_service_kit": service_kits, "inspection_kit": inspection_kits, "routine_kit": 6},
    )
    return [baseline]


def _planning_items(failure_probability: float, scenario: ResourceScenario) -> tuple[list[AssetMaintenanceInput], dict[int, InterventionAssumptions], dict[int, str]]:
    # Risk-to-deadline is an explicit planning transform, NOT RUL. It maps a 24-hour
    # failure-horizon probability to a 12-cycle (2h/cycle) scheduling urgency proxy.
    risk_deadline = max(1.5, 12.0 * (1.0 - min(max(failure_probability, 0.0), 0.95)))
    duration = max(1, round(3 * scenario.duration_multiplier))
    items = [
        AssetMaintenanceInput(
            TARGET_ASSET_ID,
            predicted_rul=risk_deadline,
            failure_probability=float(failure_probability),
            duration=duration,
            labor_hours=6.0 * duration,
            required_skill="mechanic",
            part_id="apu_service_kit",
            spare_units=1,
            preventive_cost=8_500.0,
            failure_cost=62_000.0,
            downtime_cost_per_cycle=4_500.0,
        ),
        # These are explicit assumed shop commitments, not observed MetroPT assets.
        AssetMaintenanceInput(7101, 5.0, 0.22, duration=2, labor_hours=10.0, required_skill="mechanic", part_id="routine_kit", spare_units=1, preventive_cost=4_000.0, failure_cost=18_000.0, downtime_cost_per_cycle=1_800.0),
        AssetMaintenanceInput(7102, 8.0, 0.16, duration=2, labor_hours=8.0, required_skill="electrical_tech", part_id="routine_kit", spare_units=1, preventive_cost=3_500.0, failure_cost=16_000.0, downtime_cost_per_cycle=1_500.0),
        AssetMaintenanceInput(7103, 11.0, 0.10, duration=1, labor_hours=5.0, required_skill="mechanic", part_id="routine_kit", spare_units=1, preventive_cost=2_500.0, failure_cost=12_000.0, downtime_cost_per_cycle=1_200.0),
    ]
    assumptions = {
        TARGET_ASSET_ID: InterventionAssumptions(
            inspection_cost=1_600.0,
            inspection_duration=1,
            inspection_labor_hours=3.0,
            inspection_failure_fraction=0.15,
            defer_cycles=3,
            emergency_penalty=20_000.0,
            criticality_multiplier=1.6,
        ),
        7101: InterventionAssumptions(criticality_multiplier=1.0),
        7102: InterventionAssumptions(criticality_multiplier=1.0),
        7103: InterventionAssumptions(criticality_multiplier=0.9),
    }
    provenance = {
        TARGET_ASSET_ID: "REAL_METROPT3_PREDICTION_PLUS_ASSUMED_ECONOMICS",
        7101: "ASSUMED_SHOP_BACKLOG",
        7102: "ASSUMED_SHOP_BACKLOG",
        7103: "ASSUMED_SHOP_BACKLOG",
    }
    return items, assumptions, provenance


def _risk_scenarios(items: list[AssetMaintenanceInput], probability: float) -> tuple[list[dict[int, float]], list[float]]:
    target = next(x for x in items if x.asset_id == TARGET_ASSET_ID)
    base = target.predicted_rul
    # Pessimistic/base/optimistic risk-deadline realizations; these are decision scenarios.
    scenarios = []
    for factor in (0.60, 0.80, 1.0, 1.20, 1.45):
        row = {x.asset_id: x.predicted_rul for x in items}
        row[TARGET_ASSET_ID] = max(1.0, base * factor)
        scenarios.append(row)
    # Shift probability mass pessimistically when the real-data failure-horizon risk is high.
    p = float(np.clip(probability, 0.0, 1.0))
    weights = np.asarray([0.10 + 0.25 * p, 0.18 + 0.12 * p, 0.34, 0.22 - 0.10 * p, 0.16 - 0.07 * p])
    weights = np.clip(weights, 0.02, None)
    weights = weights / weights.sum()
    return scenarios, [float(x) for x in weights]


def build_metropt_operations_case(
    metropt_root: str | Path,
    db_path: str | Path,
    *,
    at: str | None = None,
    scenario: ResourceScenario | None = None,
    solver: str = "auto",
) -> dict[str, Any]:
    runtime = load_metropt_runtime(metropt_root)
    if runtime is None:
        return {
            "status": "NOT_READY",
            "dataset_id": METROPT_DATASET_ID,
            "missing": ["full MetroPT-3 preparation pipeline has not completed"],
            "next_action": "Run scripts/prepare_metropt3.py --download",
            "claim_boundary": "No synthetic data are substituted when the real operational dataset is absent.",
        }
    feature = load_metropt_feature_store(metropt_root)
    runtime_integrity = runtime.get("integrity_check", {"status": "NOT_CHECKED", "passed": False})
    selected_ts = _select_timestamp(feature, at)
    history = feature[(feature["timestamp"] <= selected_ts) & (feature["timestamp"] >= selected_ts - pd.Timedelta(hours=8))].copy()
    current = feature[feature["timestamp"] == selected_ts].tail(1)
    scored_current = score_metropt_frame(metropt_root, current)
    row = scored_current.iloc[0]
    probability = float(row["failure_probability"])
    anomaly_score = float(row["anomaly_score"])
    scenario = scenario or ResourceScenario("baseline")
    items, assumptions, provenance = _planning_items(probability, scenario)
    risk_scenarios, risk_probabilities = _risk_scenarios(items, probability)
    profiles = _resource_profiles(12, scenario)
    plan = solve_intervention_portfolio(
        items,
        horizon=12,
        assumptions_by_asset=assumptions,
        rul_scenarios=risk_scenarios,
        scenario_probabilities=risk_probabilities,
        capacity_profiles=profiles,
        risk_aversion=0.20,
        cvar_alpha=0.90,
        allow_defer=True,
        critical_rul_threshold=2.0,
        max_deferrals=2,
        solver=solver,
    )
    target_choice = next(x for x in plan.choices if x.asset_id == TARGET_ASSET_ID)
    evidence = runtime["model"]
    quality_gate = evidence.get("quality_gate", {})
    model_ready = bool(quality_gate.get("passed") is True and quality_gate.get("promotion_allowed") is True)
    # Make observed sensor history compact enough for a browser while preserving exact derived bucket values.
    history_cols = [
        "timestamp", "TP2_mean", "TP3_mean", "Reservoirs_mean", "Oil_temperature_mean",
        "Motor_current_mean", "pressure_drop_mean", "COMP_duty", "failure_active", "failure_within_24h",
    ]
    history_records = json.loads(history[history_cols].assign(timestamp=lambda x: x["timestamp"].astype(str)).to_json(orient="records"))
    p24 = probability
    maintain_now_cost = 8_500.0 + 3.0 * 4_500.0
    inspect_positive = min(0.98, 0.20 + 0.80 * p24)
    inspect_now_cost = 1_600.0 + 0.5 * 4_500.0 + inspect_positive * maintain_now_cost + 0.15 * p24 * 82_000.0
    six_hour_exposure = 0.0 if p24 <= 0 else 1.0 - (1.0 - min(p24, 0.999)) ** (6.0 / 24.0)
    defer_six_hours_cost = 0.94 * maintain_now_cost + six_hour_exposure * (82_000.0 + 4.0 * 4_500.0)
    alternatives = [
        {"action": "maintain", "standalone_modeled_cost": float(maintain_now_cost), "downtime_hours": 6.0, "skill": "mechanic", "part": "apu_service_kit"},
        {"action": "inspect", "standalone_modeled_cost": float(inspect_now_cost), "downtime_hours": 2.0, "skill": "mechanic", "part": "inspection_kit"},
        {"action": "defer", "standalone_modeled_cost": float(defer_six_hours_cost), "downtime_hours": 0.0, "skill": None, "part": None},
    ]
    explanations = [
        f"Observed MetroPT-3 telemetry was evaluated at {selected_ts}.",
        f"The selected {evidence['selected_model']} model estimates a {probability:.1%} probability of entering a published-failure horizon within 24h.",
        f"The February-reference anomaly model score is {anomaly_score:.4f}; the operational alert threshold is {evidence['anomaly']['threshold']:.4f}.",
        f"The optimizer selected {target_choice.action.upper()} for the real APU case under {scenario.name} resource assumptions.",
        "Shop backlog, costs, technician availability and part inventory are explicit planning assumptions, not observed MetroPT fields.",
    ]
    return {
        "status": "READY",
        "source_status": "READY",
        "model_status": "PROMOTION_READY" if model_ready else "PROMOTION_BLOCKED",
        "runtime_integrity": runtime_integrity,
        "operational_readiness": "PROMOTION_READY" if model_ready else "HISTORICAL_ONLY_MODEL_GATE_BLOCKED",
        "dataset": runtime["dataset"],
        "asset": {
            "asset_id": "APU-01",
            "display_name": TARGET_ASSET_NAME,
            "timestamp": str(selected_ts),
            "evidence_class": "REAL_OPERATIONAL_TELEMETRY",
            "event_context": _event_context(selected_ts),
        },
        "observed": {
            "TP2_bar": float(row["TP2_mean"]),
            "TP3_bar": float(row["TP3_mean"]),
            "reservoirs_bar": float(row["Reservoirs_mean"]),
            "oil_temperature_c": float(row["Oil_temperature_mean"]),
            "motor_current_a": float(row["Motor_current_mean"]),
            "compressor_duty": float(row["COMP_duty"]),
            "history": history_records,
        },
        "predicted": {
            "failure_horizon_hours": int(evidence["horizon_hours"]),
            "failure_probability": probability,
            "failure_alert": bool(row["failure_alert"]),
            "anomaly_score": anomaly_score,
            "anomaly_alert": bool(row["anomaly_alert"]),
            "selected_model": evidence["selected_model"],
            "holdout_metrics": evidence["holdout_metrics"],
            "evidence_class": evidence["evidence_class"],
            "uncertainty_note": "Failure-horizon probability is model output; no certified RUL is inferred for MetroPT-3.",
        },
        "calculated": {
            "risk_equivalent_deadline_cycles": float(next(x for x in items if x.asset_id == TARGET_ASSET_ID).predicted_rul),
            "intervention_alternatives": alternatives,
            "cycle_definition": "1 planning cycle = 2 hours; risk-equivalent deadline is a decision transform, not RUL",
            "planning_economics": {
                "preventive_cost_usd": 8_500,
                "failure_cost_usd": 62_000,
                "downtime_cost_per_cycle_usd": 4_500,
                "evidence_class": "ASSUMED_PLANNING_ECONOMICS",
            },
        },
        "optimized": {
            **plan.to_dict(),
            "target_choice": target_choice.to_dict(),
            "provenance_by_asset": provenance,
            "cycle_definition": "2 hours",
            "resource_scenario": asdict(scenario),
            "evidence_class": "OPTIMIZED_WITH_REAL_RISK_AND_ASSUMED_RESOURCE_CONTEXT",
        },
        "resources": {
            "scenario": asdict(scenario),
            "assumed_backlog": [
                {"asset_id": "SHOP-7101", "skill": "mechanic", "duration_cycles": 2, "evidence_class": "ASSUMED_SHOP_BACKLOG"},
                {"asset_id": "SHOP-7102", "skill": "electrical_tech", "duration_cycles": 2, "evidence_class": "ASSUMED_SHOP_BACKLOG"},
                {"asset_id": "SHOP-7103", "skill": "mechanic", "duration_cycles": 1, "evidence_class": "ASSUMED_SHOP_BACKLOG"},
            ],
            "evidence_class": "ASSUMED_PLANNING_CONTEXT",
        },
        "work_orders": list_work_orders(db_path),
        "decision_trace": explanations,
        "claim_boundary": (
            "MetroPT sensor values and published failure windows are observed real-data evidence. "
            "Failure-horizon risk is predicted. Economics/resources are assumptions. The intervention is optimized, not realized. "
            + (
                "The supervised model promotion gate passed."
                if model_ready
                else "The supervised model promotion gate is blocked; this case is historical/testing evidence only and is not a production model approval."
            )
        ),
    }


def commit_metropt_work_order(
    metropt_root: str | Path,
    db_path: str | Path,
    *,
    at: str | None = None,
    scenario: ResourceScenario | None = None,
    actor: str = "local_operator",
    solver: str = "auto",
    require_promoted_model: bool = False,
) -> dict[str, Any]:
    case = build_metropt_operations_case(metropt_root, db_path, at=at, scenario=scenario, solver=solver)
    if case.get("status") != "READY":
        raise ValueError("MetroPT real operations case is not ready")
    if require_promoted_model and (
        case.get("model_status") != "PROMOTION_READY"
        or case.get("runtime_integrity", {}).get("passed") is not True
    ):
        raise ModelPromotionBlockedError(
            "MetroPT model promotion or runtime integrity gate is blocked; production work-order commitment is disabled"
        )
    choice = case["optimized"]["target_choice"]
    selected_ts = case["asset"]["timestamp"]
    token = pd.Timestamp(selected_ts).strftime("%Y%m%d%H%M")
    work_order_id = f"WO-METROPT-{token}-{choice['action'].upper()}"
    payload = {
        "actor": actor,
        "decision_trace": case["decision_trace"],
        "predicted": case["predicted"],
        "calculated": case["calculated"],
        "resource_scenario": case["optimized"]["resource_scenario"],
        "source_timestamp": selected_ts,
        "claim_boundary": case["claim_boundary"],
    }
    _ensure_work_orders(db_path)
    with closing(connect_state(db_path)) as con:
        con.execute(
            """INSERT INTO work_orders(work_order_id,asset_id,source_dataset,action,status,scheduled_cycle,
               duration_cycles,required_skill,part_id,evidence_class,payload)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(work_order_id) DO UPDATE SET
                 status=excluded.status, scheduled_cycle=excluded.scheduled_cycle, payload=excluded.payload,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                work_order_id,
                "APU-01",
                METROPT_DATASET_ID,
                choice["action"],
                "COMMITTED",
                choice["start_cycle"],
                int(choice["duration"]),
                choice["required_skill"],
                choice["part_id"],
                "COMMITTED_FROM_REAL_DATA_DECISION_TRACE",
                json.dumps(payload, sort_keys=True),
            ),
        )
        con.commit()
    return {"status": "COMMITTED", "work_order_id": work_order_id, "work_order": choice, "source_timestamp": selected_ts}
