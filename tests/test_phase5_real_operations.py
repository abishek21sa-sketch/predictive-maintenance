from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pdm_intelligence.operations.command import (
    build_metropt_operations_case,
    commit_metropt_work_order,
    list_work_orders,
    resource_scenario_from_name,
    update_work_order_status,
)
from pdm_intelligence.real_data.metropt import (
    ANALOG_COLUMNS,
    DIGITAL_COLUMNS,
    METROPT_FAILURE_EVENTS,
    RawValidation,
    _bootstrap_metric_intervals,
    build_runtime_evidence,
    train_metropt_models,
    validate_metropt3_csv,
)


def _structural_feature_fixture() -> pd.DataFrame:
    timestamps = pd.date_range("2020-02-01", "2020-08-20", freq="1h", inclusive="left")
    rng = np.random.default_rng(551)
    frame = pd.DataFrame({"timestamp": timestamps, "row_count": 360, "source_index_min": np.arange(len(timestamps))*360, "source_index_max": np.arange(len(timestamps))*360+359})
    hours_to_failure = np.full(len(frame), 9999.0)
    active = np.zeros(len(frame), dtype=int)
    pre24 = np.zeros(len(frame), dtype=int)
    pre6 = np.zeros(len(frame), dtype=int)
    event_id = np.array([""] * len(frame), dtype=object)
    for event in METROPT_FAILURE_EVENTS:
        delta = (event.start_ts - frame["timestamp"]).dt.total_seconds().to_numpy()/3600
        hours_to_failure = np.minimum(hours_to_failure, np.where(delta >= 0, delta, 9999.0))
        a = ((frame["timestamp"] >= event.start_ts) & (frame["timestamp"] <= event.end_ts)).to_numpy()
        p24 = ((frame["timestamp"] >= event.start_ts-pd.Timedelta(hours=24)) & (frame["timestamp"] < event.start_ts)).to_numpy()
        p6 = ((frame["timestamp"] >= event.start_ts-pd.Timedelta(hours=6)) & (frame["timestamp"] < event.start_ts)).to_numpy()
        active[a] = 1; pre24[p24] = 1; pre6[p6] = 1; event_id[a|p24] = event.event_id
    latent = pre24 * 2.2 + active * 3.0
    for i, col in enumerate(ANALOG_COLUMNS):
        base = 9.0 if col in {"TP3","Reservoirs","H1"} else (60.0 if col == "Oil_temperature" else 2.0)
        direction = -1.0 if col in {"TP3","Reservoirs","H1"} else 1.0
        mean = base + direction * latent * (0.15 + 0.02*i) + rng.normal(0, 0.08, len(frame))
        frame[f"{col}_mean"] = mean
        frame[f"{col}_std"] = 0.05 + np.abs(rng.normal(0, 0.02, len(frame))) + pre24*0.05
        frame[f"{col}_min"] = mean-frame[f"{col}_std"]
        frame[f"{col}_max"] = mean+frame[f"{col}_std"]
    for i, col in enumerate(DIGITAL_COLUMNS):
        frame[f"{col}_duty"] = np.clip(0.5 + (0.08 if i%2 else -0.08)*latent + rng.normal(0,0.03,len(frame)),0,1)
    frame["pressure_drop_mean"] = frame["TP3_mean"]-frame["Reservoirs_mean"]
    frame["compressor_to_panel_gap"] = frame["TP2_mean"]-frame["TP3_mean"]
    frame["motor_load_proxy"] = frame["Motor_current_mean"]*(1-frame["COMP_duty"])
    frame["failure_active"] = active
    frame["failure_within_24h"] = pre24
    frame["failure_within_6h"] = pre6
    frame["failure_event_id"] = event_id
    frame["hours_to_next_failure"] = np.where(hours_to_failure<9999,hours_to_failure,np.nan)
    return frame


def _prepared_root(tmp_path: Path) -> Path:
    root = tmp_path / "metropt"
    (root / "derived").mkdir(parents=True)
    frame = _structural_feature_fixture()
    frame.to_csv(root / "derived" / "metropt_feature_store.csv.gz", index=False, compression="gzip")
    model_evidence = train_metropt_models(frame, root / "models", seed=22)
    validation = RawValidation(
        row_count=1_516_948,
        columns=("X","timestamp",*ANALOG_COLUMNS,*DIGITAL_COLUMNS),
        first_timestamp="2020-02-01 00:00:00",
        last_timestamp="2020-09-01 03:59:50",
        missing_values=0,
        index_column="X",
        source_sha256="fixture-not-real-full-file",
        file_size_bytes=0,
        actual_sampling_median_seconds=10.0,
    )
    build_runtime_evidence(validation, frame, model_evidence, root / "derived" / "metropt_runtime.json")
    return root


def test_real_source_excerpt_parses_without_silent_column_loss():
    path = Path(__file__).parent / "fixtures" / "metropt_real_excerpt.csv"
    result = validate_metropt3_csv(path, strict_full_dataset=False, chunksize=3)
    assert result.row_count == 6
    assert result.index_column == "X"
    assert result.first_timestamp == "2020-02-01 00:00:00"
    assert result.last_timestamp == "2020-02-01 00:00:49"
    assert set(ANALOG_COLUMNS + DIGITAL_COLUMNS).issubset(result.columns)
    assert result.missing_values == 0


def test_clustered_bootstrap_requires_and_uses_independent_groups():
    y_true = np.array([1, 1, 0, 0, 0, 0, 0, 0])
    probability = np.array([0.9, 0.8, 0.1, 0.2, 0.3, 0.4, 0.2, 0.1])
    groups = np.array([
        "failure_event:F01",
        "failure_event:F02",
        "background_block:A",
        "background_block:A",
        "background_block:B",
        "background_block:B",
        "background_block:C",
        "background_block:C",
    ])

    intervals = _bootstrap_metric_intervals(
        y_true, probability, groups=groups, seed=19, iterations=25
    )

    assert intervals["status"] == "PASS"
    assert intervals["iterations"] == 25
    assert intervals["positive_group_count"] == 2
    assert intervals["negative_group_count"] == 3
    assert intervals["bootstrap_unit"] == "failure_event_and_background_block"


def test_real_operations_not_ready_does_not_fabricate_data(tmp_path):
    case = build_metropt_operations_case(tmp_path / "missing", tmp_path / "state.db")
    assert case["status"] == "NOT_READY"
    assert "No synthetic data" in case["claim_boundary"]


def test_time_aware_model_and_closed_loop_work_order(tmp_path):
    root = _prepared_root(tmp_path)
    metadata = json.loads((root / "derived" / "metropt_runtime.json").read_text())
    model = metadata["model"]
    assert model["partition"]["shuffle"] is False
    assert model["holdout_metrics"]["average_precision"] > model["holdout_metrics"]["prevalence"]
    assert model["quality_gate"]["event_coverage"]["holdout"]["event_count"] == 1
    assert model["quality_gate"]["event_coverage"]["passed"] is False
    assert set(model["quality_gate"]["validation_components"]) == {
        "average_precision_above_prevalence",
        "roc_auc_above_chance",
        "brier_better_than_prevalence_baseline",
        "confidence_bounds_support_gate",
    }
    assert set(model["quality_gate"]["holdout_components"]) == set(model["quality_gate"]["validation_components"])
    assert model["quality_gate"]["validation_metric_intervals"]["status"] == "INSUFFICIENT_INDEPENDENT_GROUPS"
    assert model["quality_gate"]["holdout_metric_intervals"]["status"] == "INSUFFICIENT_INDEPENDENT_GROUPS"
    assert model["quality_gate"]["validation_metric_intervals"]["bootstrap_unit"] == "failure_event_and_background_block"
    assert model["complex_model_promoted"] is False
    assert model["quality_gate"]["promotion_allowed"] is False
    runtime = json.loads((root / "derived" / "metropt_runtime.json").read_text())
    assert runtime["status"] == "REAL_DATA_PREPARED_MODEL_GATE_BLOCKED"
    assert runtime["source_status"] == "READY"
    assert runtime["model_status"] == "PROMOTION_BLOCKED"
    at = str(METROPT_FAILURE_EVENTS[-1].start_ts - pd.Timedelta(hours=3))
    db = tmp_path / "state.db"
    case = build_metropt_operations_case(root, db, at=at, scenario=resource_scenario_from_name("baseline"), solver="highs")
    assert case["status"] == "READY"
    assert case["operational_readiness"] == "HISTORICAL_ONLY_MODEL_GATE_BLOCKED"
    assert case["model_status"] == "PROMOTION_BLOCKED"
    assert case["asset"]["evidence_class"] == "REAL_OPERATIONAL_TELEMETRY"
    assert case["predicted"]["failure_horizon_hours"] == 24
    assert "RUL" in case["predicted"]["uncertainty_note"]
    assert case["optimized"]["target_choice"]["action"] in {"maintain","inspect","defer"}
    assert case["resources"]["evidence_class"] == "ASSUMED_PLANNING_CONTEXT"
    committed = commit_metropt_work_order(root, db, at=at, solver="highs")
    assert committed["status"] == "COMMITTED"
    rows = list_work_orders(db)
    assert len(rows) == 1
    assert rows[0]["source_dataset"] == "metropt-3-uci-791"
    assert rows[0]["evidence_class"] == "COMMITTED_FROM_REAL_DATA_DECISION_TRACE"
    started = update_work_order_status(db, rows[0]["work_order_id"], "IN_PROGRESS", actor="test_operator")
    assert started["status"] == "IN_PROGRESS"
    completed = update_work_order_status(db, rows[0]["work_order_id"], "COMPLETED", actor="test_operator")
    assert completed["status"] == "COMPLETED"
    assert completed["payload"]["workflow_history"][-1]["evidence_class"] == "USER_RECORDED_WORK_ORDER_STATE"
    assert "not evidence" in completed["payload"]["workflow_claim_boundary"]


def test_disruption_scenario_changes_planning_context(tmp_path):
    root = _prepared_root(tmp_path)
    at = str(METROPT_FAILURE_EVENTS[-1].start_ts - pd.Timedelta(hours=3))
    baseline = build_metropt_operations_case(root, tmp_path/"a.db", at=at, scenario=resource_scenario_from_name("baseline", bays=2), solver="highs")
    disrupted = build_metropt_operations_case(root, tmp_path/"b.db", at=at, scenario=resource_scenario_from_name("compound_disruption", bays=2), solver="highs")
    assert disrupted["resources"]["scenario"]["bay_outage"] is True
    assert disrupted["resources"]["scenario"]["technician_absent"] is True
    assert baseline["optimized"]["solver_evidence"]["status"] == "OPTIMAL"
    assert disrupted["optimized"]["solver_evidence"]["status"] == "OPTIMAL"
    assert baseline["optimized"]["solver_evidence"]["objective_value"] != disrupted["optimized"]["solver_evidence"]["objective_value"]
