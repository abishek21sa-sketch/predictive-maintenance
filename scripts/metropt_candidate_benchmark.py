"""Benchmark leakage-safe MetroPT failure-horizon candidates.

This is an investigation/acceptance aid. It evaluates candidates on the same
chronological development, validation, and future-holdout partitions used by
the production preparation path. It never changes the promotion gate and does
not write model artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesClassifier,
    GradientBoostingClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pdm_intelligence.real_data.metropt import load_metropt_feature_store


def _feature_columns(frame: pd.DataFrame) -> list[str]:
    blocked = {
        "timestamp",
        "row_count",
        "source_index_min",
        "source_index_max",
        "failure_active",
        "failure_within_24h",
        "failure_within_6h",
        "failure_event_id",
        "hours_to_next_failure",
    }
    return [
        column
        for column in frame.columns
        if column not in blocked and pd.api.types.is_numeric_dtype(frame[column])
    ]


def _baseline_columns(frame: pd.DataFrame) -> list[str]:
    names = {"TP2", "TP3", "H1", "DV_pressure", "Reservoirs", "Oil_temperature", "Motor_current"}
    return [column for column in frame.columns if column in {f"{name}_mean" for name in names}]


def _add_past_only_regime_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Add causal sensor deviations that are invariant to slow operating drift."""

    augmented = frame.copy()
    bases = [f"{column}_mean" for column in ("TP2", "TP3", "H1", "DV_pressure", "Reservoirs", "Oil_temperature", "Motor_current")]
    bases.extend(["pressure_drop_mean", "compressor_to_panel_gap", "motor_load_proxy"])
    added: list[str] = []
    for base in bases:
        short_mean = f"{base}__roll6_mean"
        long_mean = f"{base}__roll36_mean"
        short_std = f"{base}__roll6_std"
        long_std = f"{base}__roll36_std"
        if not all(name in augmented for name in (base, short_mean, long_mean, short_std, long_std)):
            continue
        trend = f"{base}__regime_trend"
        zscore = f"{base}__regime_zscore"
        volatility = f"{base}__regime_volatility_ratio"
        augmented[trend] = augmented[short_mean] - augmented[long_mean]
        augmented[zscore] = (augmented[base] - augmented[long_mean]) / (augmented[long_std].abs() + 1e-6)
        augmented[volatility] = augmented[short_std] / (augmented[long_std].abs() + 1e-6)
        added.extend((trend, zscore, volatility))
    augmented[added] = augmented[added].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return augmented, added


def _sample_weights(target: np.ndarray) -> np.ndarray:
    positive = int(target.sum())
    negative = int(len(target) - positive)
    if positive == 0 or negative == 0:
        return np.ones(len(target), dtype=float)
    return np.where(target == 1, negative / positive, 1.0).astype(float)


def _metrics(target: np.ndarray, score: np.ndarray, *, probability: bool = False) -> dict[str, float | None]:
    result: dict[str, float | None] = {
        "average_precision": float(average_precision_score(target, score)),
        "prevalence": float(target.mean()),
        "prevalence_brier": float(target.mean() * (1.0 - target.mean())),
        "roc_auc": float(roc_auc_score(target, score)),
    }
    result["brier"] = float(brier_score_loss(target, score)) if probability else None
    return result


def _evaluate(
    name: str,
    estimator: Any,
    columns: list[str],
    train: pd.DataFrame,
    validation: pd.DataFrame,
    development: pd.DataFrame,
    holdout: pd.DataFrame,
    target: str,
    *,
    sample_weight: np.ndarray | None = None,
) -> dict[str, Any]:
    y_train = train[target].astype(int).to_numpy()
    y_validation = validation[target].astype(int).to_numpy()
    y_development = development[target].astype(int).to_numpy()
    y_holdout = holdout[target].astype(int).to_numpy()
    if sample_weight is None:
        estimator.fit(train[columns], y_train)
    else:
        estimator.fit(train[columns], y_train, sample_weight=sample_weight)
    validation_score = estimator.predict_proba(validation[columns])[:, 1]
    if sample_weight is None:
        estimator.fit(development[columns], y_development)
    else:
        estimator.fit(development[columns], y_development, sample_weight=_sample_weights(y_development))
    holdout_score = estimator.predict_proba(holdout[columns])[:, 1]
    return {
        "name": name,
        "feature_count": len(columns),
        "validation": _metrics(y_validation, validation_score, probability=True),
        "holdout": _metrics(y_holdout, holdout_score, probability=True),
    }


def benchmark(root: str | Path, *, only: set[str] | None = None) -> dict[str, Any]:
    root = Path(root)
    frame = load_metropt_feature_store(root)
    frame, regime_features = _add_past_only_regime_features(frame)
    usable = frame[frame["failure_active"] == 0].copy()
    target = "failure_within_24h"
    fit_end = pd.Timestamp("2020-06-01 00:00:00")
    validation_end = pd.Timestamp("2020-07-01 00:00:00")
    train = usable[usable["timestamp"] < fit_end]
    validation = usable[(usable["timestamp"] >= fit_end) & (usable["timestamp"] < validation_end)]
    development = usable[usable["timestamp"] < validation_end]
    holdout = usable[usable["timestamp"] >= validation_end]
    features = _feature_columns(usable)
    baseline = _baseline_columns(usable)
    regime = list(dict.fromkeys(features + regime_features))

    anomaly = joblib.load(root / "models" / "metropt_anomaly.joblib")
    usable["anomaly_score"] = -anomaly["model"].decision_function(
        anomaly["scaler"].transform(usable[anomaly["features"]])
    )
    train = usable[usable["timestamp"] < fit_end]
    validation = usable[(usable["timestamp"] >= fit_end) & (usable["timestamp"] < validation_end)]
    development = usable[usable["timestamp"] < validation_end]
    holdout = usable[usable["timestamp"] >= validation_end]
    feature_sets = {
        "baseline": baseline,
        "all_engineered": features,
        "baseline_plus_anomaly": baseline + ["anomaly_score"],
        "all_engineered_plus_anomaly": features + ["anomaly_score"],
        "regime_normalized": regime,
    }
    candidates: list[tuple[str, Any, str, bool]] = [
        (
            "logistic_baseline",
            Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(C=1.0, max_iter=1200, class_weight="balanced", random_state=731)),
                ]
            ),
            "baseline",
            False,
        ),
        (
            "logistic_unweighted_baseline",
            Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(C=1.0, max_iter=1200, random_state=731)),
                ]
            ),
            "baseline",
            False,
        ),
        (
            "logistic_unweighted_baseline_regularized",
            Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(C=0.01, max_iter=1200, random_state=731)),
                ]
            ),
            "baseline",
            False,
        ),
        (
            "logistic_unweighted_engineered",
            Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(C=0.1, max_iter=1200, random_state=731)),
                ]
            ),
            "all_engineered",
            False,
        ),
        (
            "logistic_regime_normalized_unweighted",
            Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(C=0.1, max_iter=1200, random_state=731)),
                ]
            ),
            "regime_normalized",
            False,
        ),
        (
            "logistic_baseline_plus_anomaly",
            Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(C=1.0, max_iter=1200, class_weight="balanced", random_state=731)),
                ]
            ),
            "baseline_plus_anomaly",
            False,
        ),
        (
            "logistic_balanced",
            Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(C=1.0, max_iter=1200, class_weight="balanced", random_state=731)),
                ]
            ),
            "all_engineered_plus_anomaly",
            False,
        ),
        (
            "logistic_regularized",
            Pipeline(
                [
                    ("scale", StandardScaler()),
                    ("model", LogisticRegression(C=0.1, max_iter=1200, class_weight="balanced", random_state=731)),
                ]
            ),
            "all_engineered_plus_anomaly",
            False,
        ),
        (
            "extra_trees",
            ExtraTreesClassifier(
                n_estimators=300,
                max_depth=12,
                min_samples_leaf=4,
                class_weight="balanced",
                random_state=731,
                n_jobs=1,
            ),
            "all_engineered_plus_anomaly",
            False,
        ),
        (
            "random_forest_unweighted",
            RandomForestClassifier(
                n_estimators=300,
                max_depth=12,
                min_samples_leaf=10,
                random_state=731,
                n_jobs=1,
            ),
            "all_engineered",
            False,
        ),
        (
            "random_forest_deeper",
            RandomForestClassifier(
                n_estimators=300,
                max_depth=20,
                min_samples_leaf=2,
                class_weight="balanced",
                random_state=731,
                n_jobs=1,
            ),
            "all_engineered_plus_anomaly",
            False,
        ),
        (
            "hist_gradient_boosting",
            HistGradientBoostingClassifier(
                learning_rate=0.03,
                max_iter=300,
                max_leaf_nodes=31,
                min_samples_leaf=15,
                l2_regularization=2.0,
                random_state=731,
            ),
            "all_engineered_plus_anomaly",
            True,
        ),
        (
            "hist_gradient_boosting_unweighted",
            HistGradientBoostingClassifier(
                learning_rate=0.03,
                max_iter=300,
                max_leaf_nodes=31,
                min_samples_leaf=15,
                l2_regularization=2.0,
                random_state=731,
            ),
            "all_engineered",
            False,
        ),
        (
            "gradient_boosting",
            GradientBoostingClassifier(
                n_estimators=150,
                max_depth=2,
                min_samples_leaf=12,
                learning_rate=0.03,
                random_state=731,
            ),
            "all_engineered_plus_anomaly",
            False,
        ),
    ]
    if only:
        candidates = [candidate for candidate in candidates if candidate[0] in only]
    results = [
        {
            "anomaly_score_only": {
                "validation": _metrics(
                    validation[target].astype(int).to_numpy(), validation["anomaly_score"].to_numpy()
                ),
                "holdout": _metrics(holdout[target].astype(int).to_numpy(), holdout["anomaly_score"].to_numpy()),
            }
        }
    ]
    for name, estimator, feature_set, weighted in candidates:
        results.append(
            _evaluate(
                name,
                estimator,
                feature_sets[feature_set],
                train,
                validation,
                development,
                holdout,
                target,
                sample_weight=_sample_weights(train[target].astype(int).to_numpy()) if weighted else None,
            )
        )
    return {
        "dataset_root": str(root),
        "target": target,
        "partition": {
            "fit_before": str(fit_end),
            "validation": f"{fit_end} to {validation_end}",
            "holdout_from": str(validation_end),
            "shuffle": False,
        },
        "gate_boundary": (
            "Results are exploratory until the production preparation path records them. Promotion still "
            "requires validation and future-holdout AP above prevalence, ROC-AUC above chance, Brier loss "
            "better than the constant-prevalence baseline, event/block-clustered 95% bootstrap bounds supporting "
            "those comparisons, and at least two independent published failure windows in each evaluation "
            "period."
        ),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark leakage-safe MetroPT failure-horizon candidates")
    parser.add_argument("--root", default="data/external/metropt3")
    parser.add_argument("--output", help="Optional JSON evidence path; no model artifacts are written")
    parser.add_argument(
        "--only",
        help="Comma-separated candidate names for a bounded experiment; defaults to all candidates",
    )
    args = parser.parse_args()
    selected = {name.strip() for name in args.only.split(",") if name.strip()} if args.only else None
    payload = json.dumps(benchmark(args.root, only=selected), indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
