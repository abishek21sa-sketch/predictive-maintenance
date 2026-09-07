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
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="data/raw")
    p.add_argument("--out", default="artifacts/reports/fd001_benchmark.json")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()
    d = Path(args.data_dir)
    train_path, test_path, rul_path = d/"train_FD001.txt", d/"test_FD001.txt", d/"RUL_FD001.txt"
    train = add_training_rul(load_trajectory(train_path), cap=125)
    test = load_trajectory(test_path)
    true = load_rul(rul_path).to_numpy(float)

    result = train_rul_models(train, validation_fraction=0.25, seed=args.seed)
    pred_df = predict_latest(result.model, result.feature_columns, test).sort_values("unit_id")
    pred = pred_df["predicted_rul"].to_numpy(float)
    metrics = regression_metrics(true, pred)
    metrics["nasa_score"] = nasa_asymmetric_score(true, pred)
    test_groups = pred_df["unit_id"].to_numpy()
    lower, upper = residual_interval(pred, result.residual_interval_90)
    interval_evidence = {
        "half_width_cycles": result.residual_interval_90,
        "validation_coverage_90": result.interval_coverage_90,
        "official_test_coverage_90": float(np.mean((true >= lower) & (true <= upper))),
        "claim_boundary": (
            "This is an empirical symmetric residual band estimated from the asset holdout. "
            "It is not a formal conformal-coverage guarantee."
        ),
    }
    metric_intervals = grouped_bootstrap_regression_metrics(
        true,
        pred,
        test_groups,
        seed=args.seed,
        residual_half_width=result.residual_interval_90,
    )

    # Age-only baseline is fit on all labeled training cycles and evaluated on each test engine's terminal cycle.
    baseline = Ridge(alpha=1.0).fit(train[["cycle"]], train["rul"])
    latest_test = test.sort_values(["unit_id","cycle"]).groupby("unit_id").tail(1).sort_values("unit_id")
    baseline_pred = np.maximum(0.0, baseline.predict(latest_test[["cycle"]]))
    baseline_metrics = regression_metrics(true, baseline_pred)
    baseline_metrics["nasa_score"] = nasa_asymmetric_score(true, baseline_pred)

    payload = {
        "dataset": "NASA C-MAPSS FD001",
        "train_rows": len(train),
        "test_rows": len(test),
        "train_units": int(train.unit_id.nunique()),
        "test_units": int(test.unit_id.nunique()),
        "selected_model": result.selected_model,
        "asset_holdout_metrics": result.metrics,
        "official_test_metrics": metrics,
        "official_test_baseline_metrics": baseline_metrics,
        "official_test_metric_intervals": metric_intervals,
        "rul_interval_evidence": interval_evidence,
        "top_features": result.top_features,
        "source_reference": "https://data.nasa.gov/dataset/cmapss-jet-engine-simulated-data",
        "raw_data_policy": "public benchmark retained locally; raw files excluded from release artifacts",
        "checksums": {p.name: sha256(p) for p in (train_path,test_path,rul_path)},
        "seed": args.seed,
    }
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
