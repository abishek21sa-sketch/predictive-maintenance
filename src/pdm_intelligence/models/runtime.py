from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from pdm_intelligence.data.cmapss import validate
from pdm_intelligence.decision.engine import failure_risk_from_rul
from pdm_intelligence.features.engineering import engineer_features
from pdm_intelligence.models.rul import predict_latest, residual_interval


def release_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "release"


@lru_cache(maxsize=1)
def load_release_models():
    base = release_dir()
    metadata = json.loads((base / "fd001_model_metadata.json").read_text(encoding="utf-8"))
    rul_model = joblib.load(base / "fd001_rul_model.joblib")
    anomaly_model = joblib.load(base / "fd001_anomaly_model.joblib")
    return rul_model, anomaly_model, metadata


def score_trajectory(records: list[dict]) -> list[dict]:
    if not records:
        raise ValueError("At least one trajectory record is required")
    raw = validate(pd.DataFrame(records))
    model, anomaly, metadata = load_release_models()
    pred = predict_latest(model, metadata["features"], raw)
    feat = engineer_features(raw)
    latest_feat = feat.sort_values(["unit_id", "cycle"]).groupby("unit_id").tail(1).copy()
    # Ensemble dispersion is exposed as a transparent model-uncertainty interval.
    # It is not claimed to be a statistically calibrated predictive interval.
    if hasattr(model, "estimators_"):
        tree_preds = np.vstack([est.predict(latest_feat[metadata["features"]]) for est in model.estimators_])
        pred["rul_p10"] = np.maximum(0.0, np.quantile(tree_preds, 0.10, axis=0))
        pred["rul_p90"] = np.maximum(0.0, np.quantile(tree_preds, 0.90, axis=0))
        pred["uncertainty_method"] = "random_forest_ensemble_p10_p90"
    else:
        half_width = metadata.get("residual_interval_90")
        if half_width is not None and np.isfinite(float(half_width)):
            lower, upper = residual_interval(pred["predicted_rul"], float(half_width))
            pred["rul_p10"] = lower
            pred["rul_p90"] = upper
            pred["uncertainty_method"] = metadata.get(
                "uncertainty_method", "asset_holdout_empirical_residual_band_90"
            )
        else:
            pred["rul_p10"] = np.nan
            pred["rul_p90"] = np.nan
            pred["uncertainty_method"] = "not_available"
    latest = raw.sort_values(["unit_id", "cycle"]).groupby("unit_id").tail(1).copy()
    latest["anomaly_score"] = anomaly.score(latest).values
    out = pred.merge(latest[["unit_id", "anomaly_score"]], on="unit_id")
    out["failure_risk"] = out["predicted_rul"].map(failure_risk_from_rul)
    return out.round(4).to_dict("records")
