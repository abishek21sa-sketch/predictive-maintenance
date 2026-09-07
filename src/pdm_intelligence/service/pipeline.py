from __future__ import annotations

from dataclasses import dataclass

from pdm_intelligence.data.cmapss import add_training_rul
from pdm_intelligence.data.synthetic import generate_cmapss_fixture
from pdm_intelligence.decision.engine import decide, failure_risk_from_rul
from pdm_intelligence.models.anomaly import AnomalyDetector
from pdm_intelligence.models.rul import predict_latest, train_rul_models
from pdm_intelligence.optimization.maintenance import (
    AssetMaintenanceInput,
    optimize_maintenance_stochastic,
)
from pdm_intelligence.reliability.analysis import fit_reliability
from pdm_intelligence.simulation.lifecycle import compare_policies, optimize_predictive_threshold


@dataclass
class DemoResult:
    metrics: dict
    baseline_metrics: dict
    reliability: dict
    predictions: list[dict]
    decisions: list[dict]
    simulation: list[dict]
    candidate_metrics: dict
    selected_model: str
    top_features: list[dict]
    simulation_optimization: dict


def run_demo(seed: int = 42, n_units: int = 40) -> DemoResult:
    # Full run-to-failure histories are used for training/reliability. The operational
    # inference view is deliberately right-censored so the platform predicts before failure.
    raw = generate_cmapss_fixture(n_units=n_units, seed=seed)
    labeled = add_training_rul(raw)
    training = train_rul_models(labeled, seed=seed)
    anomaly = AnomalyDetector(seed=seed).fit(raw)

    import numpy as np
    rng = np.random.default_rng(seed + 101)
    max_cycles = raw.groupby("unit_id")["cycle"].max().to_dict()
    cutoffs = {
        int(unit): max(20, int(life * rng.uniform(0.62, 0.90)))
        for unit, life in max_cycles.items()
    }
    operational = raw[raw.apply(lambda r: r.cycle <= cutoffs[int(r.unit_id)], axis=1)].copy()

    latest = predict_latest(training.model, training.feature_columns, operational)
    latest_rows = operational.sort_values(["unit_id", "cycle"]).groupby("unit_id").tail(1).copy()
    latest_rows["anomaly_score"] = anomaly.score(latest_rows).values
    latest_rows["true_rul_fixture"] = latest_rows.apply(
        lambda r: float(max_cycles[int(r.unit_id)] - r.cycle), axis=1
    )
    merged = latest.merge(
        latest_rows[["unit_id", "anomaly_score", "true_rul_fixture"]], on="unit_id"
    )
    merged["failure_risk"] = merged["predicted_rul"].map(failure_risk_from_rul)

    opt_inputs = [
        AssetMaintenanceInput(int(r.unit_id), float(r.predicted_rul), float(r.failure_risk))
        for r in merged.itertuples()
    ]
    scenarios = [
        {int(r.unit_id): max(1.0, float(r.predicted_rul) * factor) for r in merged.itertuples()}
        for factor in (0.80, 1.00, 1.20)
    ]
    schedule = optimize_maintenance_stochastic(
        opt_inputs, scenarios, probabilities=[0.20, 0.60, 0.20], horizon=30, capacity_per_cycle=2
    )
    slot = {s.asset_id: s.cycle for s in schedule}
    decisions = [
        decide(int(r.unit_id), int(r.cycle), float(r.predicted_rul), float(r.anomaly_score), slot[int(r.unit_id)]).to_dict()
        for r in merged.itertuples()
    ]
    lives = raw.groupby("unit_id")["cycle"].max().to_numpy()
    reliability = fit_reliability(lives).to_dict()
    sim = [x.to_dict() for x in compare_policies(merged["predicted_rul"].to_numpy(), n_simulations=500, seed=seed)]
    sim_opt = optimize_predictive_threshold(merged["predicted_rul"].to_numpy(), n_simulations=300, seed=seed)
    return DemoResult(
        metrics=training.metrics,
        baseline_metrics=training.baseline_metrics,
        reliability=reliability,
        predictions=merged.round(4).to_dict("records"),
        decisions=decisions,
        simulation=sim,
        candidate_metrics=training.candidate_metrics,
        selected_model=training.selected_model,
        top_features=training.top_features,
        simulation_optimization=sim_opt,
    )
