from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from pdm_intelligence.features.engineering import engineer_features, model_feature_columns


@dataclass
class RULTrainingResult:
    model: object
    feature_columns: list[str]
    metrics: dict[str, float]
    baseline_metrics: dict[str, float]
    validation_units: list[int]
    candidate_metrics: dict[str, dict[str, float]]
    selected_model: str
    top_features: list[dict[str, float | str]]
    residual_interval_90: float
    interval_coverage_90: float


def regression_metrics(y_true, y_pred) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": float(r2_score(y_true, y_pred)),
    }


def nasa_asymmetric_score(y_true, y_pred) -> float:
    """NASA PHM score: late RUL predictions receive the steeper exponential penalty."""
    d = np.asarray(y_pred, dtype=float) - np.asarray(y_true, dtype=float)
    penalties = np.where(d < 0, np.exp(-d / 13.0) - 1.0, np.exp(d / 10.0) - 1.0)
    return float(np.sum(penalties))


def train_rul_models(df: pd.DataFrame, validation_fraction: float = 0.25, seed: int = 42) -> RULTrainingResult:
    if "rul" not in df:
        raise ValueError("Training dataframe requires a rul target")
    feat = engineer_features(df)
    features = model_feature_columns(feat)
    units = np.array(sorted(feat.unit_id.unique()))
    rng = np.random.default_rng(seed)
    rng.shuffle(units)
    n_val = max(1, round(len(units) * validation_fraction))
    val_units = set(map(int, units[:n_val]))
    train = feat[~feat.unit_id.isin(val_units)]
    valid = feat[feat.unit_id.isin(val_units)]
    X_train, y_train = train[features], train["rul"]
    X_valid, y_valid = valid[features], valid["rul"]

    baseline = Ridge(alpha=1.0).fit(train[["cycle"]], y_train)
    baseline_pred = np.maximum(0, baseline.predict(valid[["cycle"]]))
    baseline_metrics = regression_metrics(y_valid, baseline_pred)

    candidates = {
        "ridge": Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=5.0))]),
        "random_forest": RandomForestRegressor(
            n_estimators=80, max_depth=12, min_samples_leaf=3, random_state=seed, n_jobs=1
        ),
        "hist_gradient_boosting": HistGradientBoostingRegressor(
            max_depth=8, learning_rate=0.08, max_iter=120, l2_regularization=0.2, random_state=seed
        ),
    }
    scores: dict[str, dict[str, float]] = {}
    fitted = {}
    for name, model in candidates.items():
        m = clone(model).fit(X_train, y_train)
        pred = np.maximum(0, m.predict(X_valid))
        scores[name] = regression_metrics(y_valid, pred)
        scores[name]["nasa_score"] = nasa_asymmetric_score(y_valid, pred)
        fitted[name] = m

    best_name = min(scores, key=lambda n: scores[n]["rmse"])
    best_holdout = fitted[best_name]
    best_valid_pred = np.maximum(0, best_holdout.predict(X_valid))
    # Empirical holdout residual band. This quantifies validation residual dispersion;
    # it is not represented as a formal conformal guarantee because model selection
    # and interval estimation share the same asset holdout.
    abs_residual = np.abs(np.asarray(y_valid, dtype=float) - best_valid_pred)
    residual_interval_90 = float(np.quantile(abs_residual, 0.90))
    interval_coverage_90 = float(np.mean(abs_residual <= residual_interval_90))

    # Model-level explainability on a bounded validation sample keeps CI deterministic and fast.
    sample_n = min(250, len(X_valid))
    sample = X_valid.sample(sample_n, random_state=seed)
    sample_y = y_valid.loc[sample.index]
    perm = permutation_importance(
        best_holdout, sample, sample_y, n_repeats=1, random_state=seed,
        scoring="neg_root_mean_squared_error", n_jobs=1
    )
    order = np.argsort(perm.importances_mean)[::-1][:12]
    top_features = [
        {"feature": features[i], "importance": float(perm.importances_mean[i])}
        for i in order if perm.importances_mean[i] > 0
    ]

    # Refit the selected algorithm on all training assets only after holdout selection/evaluation.
    final_model = clone(candidates[best_name]).fit(feat[features], feat["rul"])
    metrics = dict(scores[best_name])
    return RULTrainingResult(
        model=final_model,
        feature_columns=features,
        metrics=metrics,
        baseline_metrics=baseline_metrics,
        validation_units=sorted(val_units),
        candidate_metrics=scores,
        selected_model=best_name,
        top_features=top_features,
        residual_interval_90=residual_interval_90,
        interval_coverage_90=interval_coverage_90,
    )


def predict_latest(model, feature_columns: list[str], df: pd.DataFrame) -> pd.DataFrame:
    feat = engineer_features(df)
    latest = feat.sort_values(["unit_id", "cycle"]).groupby("unit_id").tail(1).copy()
    latest["predicted_rul"] = np.maximum(0.0, model.predict(latest[feature_columns]))
    return latest[["unit_id", "cycle", "predicted_rul"]]


def residual_interval(predictions, half_width: float):
    """Symmetric empirical residual interval around non-negative RUL predictions."""
    pred = np.maximum(0.0, np.asarray(predictions, dtype=float))
    width = max(0.0, float(half_width))
    return np.maximum(0.0, pred - width), pred + width


def grouped_bootstrap_regression_metrics(
    y_true,
    y_pred,
    groups,
    *,
    seed: int = 42,
    iterations: int = 2_000,
    confidence: float = 0.95,
    residual_half_width: float | None = None,
) -> dict[str, object]:
    """Estimate asset-grouped confidence intervals for regression evidence.

    Complete asset trajectories are resampled as units so serial rows are not treated as
    independent observations.  The optional residual band is evaluated on each resample, but
    remains an empirical diagnostic rather than a formal conformal-coverage guarantee.
    """
    truth = np.asarray(y_true, dtype=float)
    prediction = np.asarray(y_pred, dtype=float)
    labels = np.asarray(groups)
    if truth.shape != prediction.shape or truth.ndim != 1:
        raise ValueError("y_true and y_pred must be one-dimensional arrays with equal length")
    if labels.shape != truth.shape:
        raise ValueError("groups must have the same length as y_true")
    if iterations < 1:
        raise ValueError("iterations must be positive")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    unique_groups = np.unique(labels)
    base = {
        "bootstrap_unit": "asset_trajectory",
        "confidence_level": float(confidence),
        "group_count": len(unique_groups),
    }
    if len(unique_groups) < 2:
        return {**base, "iterations": 0, "status": "INSUFFICIENT_INDEPENDENT_GROUPS"}

    group_indices = [np.flatnonzero(labels == group) for group in unique_groups]
    rng = np.random.default_rng(seed)
    values: dict[str, list[float]] = {"mae": [], "rmse": [], "r2": [], "nasa_score": []}
    if residual_half_width is not None:
        values["interval_coverage"] = []
    for _ in range(iterations):
        selected = rng.integers(0, len(group_indices), size=len(group_indices))
        indices = np.concatenate([group_indices[index] for index in selected])
        sample_true = truth[indices]
        sample_pred = prediction[indices]
        metrics = regression_metrics(sample_true, sample_pred)
        values["mae"].append(metrics["mae"])
        values["rmse"].append(metrics["rmse"])
        values["r2"].append(metrics["r2"])
        values["nasa_score"].append(nasa_asymmetric_score(sample_true, sample_pred))
        if residual_half_width is not None:
            lower, upper = residual_interval(sample_pred, residual_half_width)
            values["interval_coverage"].append(
                float(np.mean((sample_true >= lower) & (sample_true <= upper)))
            )
    tail = (1.0 - confidence) / 2.0
    intervals = {
        name: {
            "low": float(np.quantile(samples, tail)),
            "median": float(np.quantile(samples, 0.5)),
            "high": float(np.quantile(samples, 1.0 - tail)),
        }
        for name, samples in values.items()
    }
    return {
        **base,
        "iterations": int(iterations),
        "status": "PASS",
        "intervals": intervals,
        "claim_boundary": (
            "Asset-grouped bootstrap intervals quantify benchmark uncertainty; they do not certify "
            "field reliability, causal benefit, or calibrated prediction coverage."
        ),
    }
