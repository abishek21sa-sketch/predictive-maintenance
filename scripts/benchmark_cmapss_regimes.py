"""Benchmark the same leakage-safe RUL pipeline across NASA C-MAPSS regimes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge

from pdm_intelligence.data.cmapss import add_training_rul, load_rul, load_trajectory
from pdm_intelligence.models.rul import (
    grouped_bootstrap_regression_metrics,
    nasa_asymmetric_score,
    predict_latest,
    regression_metrics,
    residual_interval,
    train_rul_models,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def benchmark_subset(data_dir: Path, subset: str, seed: int) -> dict[str, object]:
    subset = subset.upper()
    train_path = data_dir / f"train_{subset}.txt"
    test_path = data_dir / f"test_{subset}.txt"
    rul_path = data_dir / f"RUL_{subset}.txt"
    train = add_training_rul(load_trajectory(train_path), cap=125)
    test = load_trajectory(test_path)
    truth = load_rul(rul_path).to_numpy(float)

    trained = train_rul_models(train, validation_fraction=0.25, seed=seed)
    predictions = predict_latest(trained.model, trained.feature_columns, test).sort_values("unit_id")
    predicted = predictions["predicted_rul"].to_numpy(float)
    metrics = regression_metrics(truth, predicted)
    metrics["nasa_score"] = nasa_asymmetric_score(truth, predicted)

    latest_test = test.sort_values(["unit_id", "cycle"]).groupby("unit_id").tail(1).sort_values("unit_id")
    baseline_model = Ridge(alpha=1.0).fit(train[["cycle"]], train["rul"])
    baseline_predicted = np.maximum(0.0, baseline_model.predict(latest_test[["cycle"]]))
    baseline_metrics = regression_metrics(truth, baseline_predicted)
    baseline_metrics["nasa_score"] = nasa_asymmetric_score(truth, baseline_predicted)

    lower, upper = residual_interval(predicted, trained.residual_interval_90)
    metric_intervals = grouped_bootstrap_regression_metrics(
        truth,
        predicted,
        predictions["unit_id"].to_numpy(),
        seed=seed,
        iterations=1_000,
        residual_half_width=trained.residual_interval_90,
    )
    return {
        "subset": subset,
        "train_rows": len(train),
        "test_rows": len(test),
        "train_units": int(train.unit_id.nunique()),
        "test_units": int(test.unit_id.nunique()),
        "selected_model": trained.selected_model,
        "asset_holdout_metrics": trained.metrics,
        "official_test_metrics": metrics,
        "official_test_baseline_metrics": baseline_metrics,
        "official_test_metric_intervals": metric_intervals,
        "rul_interval_evidence": {
            "half_width_cycles": trained.residual_interval_90,
            "validation_coverage_90": trained.interval_coverage_90,
            "official_test_coverage_90": float(np.mean((truth >= lower) & (truth <= upper))),
            "claim_boundary": (
                "Empirical asset-holdout residual coverage is reported for benchmark diagnostics; "
                "it is not a formal conformal guarantee or field reliability certification."
            ),
        },
        "source_checksums": {
            path.name: sha256(path) for path in (train_path, test_path, rul_path)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark NASA C-MAPSS FD001-FD004 regimes")
    parser.add_argument("--data-dir", default="data/raw/CMAPSSData")
    parser.add_argument("--out", default="artifacts/reports/cmapss_regime_benchmark.json")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--subsets", nargs="+", default=["FD001", "FD002", "FD003", "FD004"])
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    results = [benchmark_subset(data_dir, subset, args.seed) for subset in args.subsets]
    aggregate = {
        metric: float(np.mean([float(result["official_test_metrics"][metric]) for result in results]))
        for metric in ("mae", "rmse", "r2", "nasa_score")
    }
    payload = {
        "dataset": "NASA C-MAPSS FD001-FD004",
        "regime_count": len(results),
        "regimes": results,
        "aggregate_official_test_metrics_mean": aggregate,
        "validation_design": (
            "Each regime is evaluated independently with complete-engine holdout model selection; "
            "no row-level random split and no cross-regime metric transfer."
        ),
        "raw_data_policy": "public benchmark retained locally; raw files excluded from release artifacts",
        "claim_boundary": (
            "These are public simulated benchmark results across four C-MAPSS regimes. They do not "
            "certify MetroPT transfer, field reliability, causal benefit, or production readiness."
        ),
        "seed": args.seed,
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
