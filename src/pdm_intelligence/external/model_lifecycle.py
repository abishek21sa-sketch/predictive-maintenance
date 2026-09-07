from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from pdm_intelligence.models.model_registry import ModelRegistry

from .catalog import ExternalCatalog

MIN_EXTERNAL_TRAINING_ASSETS = 6
MIN_EXTERNAL_VALIDATION_ASSETS = 2
MODEL_PROMOTION_GATE_VERSION = 2


@dataclass(frozen=True)
class ExternalRULTrainingResult:
    model_id: str
    dataset_id: str
    selected_model: str
    features: list[str]
    validation_assets: list[str]
    metrics: dict[str, float]
    baseline_metrics: dict[str, float]
    candidate_metrics: dict[str, dict[str, float]]
    residual_interval_90: float
    interval_coverage_90: float
    promotion_gate: dict[str, Any]
    evidence_class: str = "VALIDATED_ON_USER_SUPPLIED_HOLDOUT"

    def to_dict(self):
        return asdict(self)


def _metrics(y, pred):
    return {
        "mae": float(mean_absolute_error(y, pred)),
        "rmse": float(np.sqrt(mean_squared_error(y, pred))),
        "r2": float(r2_score(y, pred)),
    }


def engineer_generic_features(df: pd.DataFrame, window: int = 5) -> tuple[pd.DataFrame, list[str]]:
    if "asset_id" not in df or "rul" not in df:
        raise ValueError("External RUL training requires asset_id and rul")
    axis = "cycle" if "cycle" in df else "timestamp" if "timestamp" in df else None
    if axis is None:
        raise ValueError("External RUL training requires cycle or timestamp")
    out = df.sort_values(["asset_id", axis]).copy()
    excluded = {"asset_id", "cycle", "timestamp", "rul"}
    leakage_tokens = ("rul", "remaining_life", "remaining_useful", "time_to_failure", "failure_cycle", "target")
    suspicious = {c for c in out.columns if c not in excluded and any(token in c.lower() for token in leakage_tokens)}
    excluded |= suspicious
    signals = [c for c in out.columns if c not in excluded and pd.api.types.is_numeric_dtype(out[c])]
    if not signals:
        raise ValueError("External RUL training requires numeric sensor/condition signals")
    g = out.groupby("asset_id", group_keys=False)
    for c in signals:
        out[f"{c}__delta"] = g[c].diff().fillna(0.0)
        out[f"{c}__mean{window}"] = g[c].transform(lambda s: s.rolling(window, min_periods=1).mean())
        out[f"{c}__std{window}"] = g[c].transform(lambda s: s.rolling(window, min_periods=2).std().fillna(0.0))
    if axis == "timestamp":
        first = g[axis].transform("min")
        out["elapsed_hours"] = (out[axis] - first).dt.total_seconds() / 3600.0
    numeric = [c for c in out.columns if c not in ({"asset_id", "rul", "timestamp"} | suspicious) and pd.api.types.is_numeric_dtype(out[c])]
    return out, numeric


def train_external_rul(
    telemetry: pd.DataFrame,
    dataset_id: str,
    output_root: str | Path = "data/external_models",
    catalog_path: str | Path = "data/pdm.db",
    seed: int = 42,
    validation_fraction: float = 0.25,
    evidence_class: str | None = None,
) -> ExternalRULTrainingResult:
    catalog = ExternalCatalog(catalog_path)
    dataset_record = catalog.get_dataset(dataset_id)
    recorded_evidence_class = str((dataset_record or {}).get("evidence_class") or "").strip().upper()
    if evidence_class:
        resolved_evidence_class = str(evidence_class).strip().upper()
    elif recorded_evidence_class == "SYNTHETIC_TEST_ONLY":
        resolved_evidence_class = "SYNTHETIC_TEST_ONLY"
    else:
        resolved_evidence_class = "VALIDATED_ON_USER_SUPPLIED_HOLDOUT"
    if not resolved_evidence_class:
        raise ValueError("evidence_class must not be blank")
    asset_count = telemetry["asset_id"].nunique()
    if asset_count < MIN_EXTERNAL_TRAINING_ASSETS:
        raise ValueError(
            f"At least {MIN_EXTERNAL_TRAINING_ASSETS} independent asset trajectories are required "
            "for a two-asset holdout and four-asset training population"
        )
    feat, features = engineer_generic_features(telemetry)
    assets = np.array(sorted(feat["asset_id"].astype(str).unique()))
    rng = np.random.default_rng(seed)
    rng.shuffle(assets)
    n_val = max(MIN_EXTERNAL_VALIDATION_ASSETS, round(len(assets) * validation_fraction))
    n_val = min(n_val, len(assets) - 4)
    if n_val < MIN_EXTERNAL_VALIDATION_ASSETS or len(assets) - n_val < 4:
        raise ValueError(
            "Unable to create the required independent asset-level holdout; "
            "use at least 6 trajectories"
        )
    val_assets = set(assets[:n_val].tolist())
    train = feat[~feat["asset_id"].astype(str).isin(val_assets)]
    valid = feat[feat["asset_id"].astype(str).isin(val_assets)]
    if train.empty or valid.empty:
        raise ValueError("Unable to create non-empty asset-level train/validation split")

    baseline_feature = "cycle" if "cycle" in train else "elapsed_hours"
    baseline = Ridge(alpha=1.0).fit(train[[baseline_feature]], train["rul"])
    baseline_pred = np.maximum(0.0, baseline.predict(valid[[baseline_feature]]))
    baseline_metrics = _metrics(valid["rul"], baseline_pred)

    candidates = {
        "ridge": Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=5.0))]),
        "random_forest": RandomForestRegressor(
            n_estimators=100, max_depth=12, min_samples_leaf=2, random_state=seed, n_jobs=1
        ),
        "hist_gradient_boosting": HistGradientBoostingRegressor(
            max_depth=8, learning_rate=0.08, max_iter=140, l2_regularization=0.2, random_state=seed
        ),
    }
    fitted = {}
    scores = {}
    for name, candidate in candidates.items():
        model = clone(candidate).fit(train[features], train["rul"])
        pred = np.maximum(0.0, model.predict(valid[features]))
        fitted[name] = model
        scores[name] = _metrics(valid["rul"], pred)
    selected = min(scores, key=lambda n: scores[n]["rmse"])
    valid_pred = np.maximum(0.0, fitted[selected].predict(valid[features]))
    residual = np.abs(valid["rul"].to_numpy(float) - valid_pred)
    width = float(np.quantile(residual, 0.90))
    coverage = float(np.mean(residual <= width))
    train_assets = set(train["asset_id"].astype(str).unique())
    validation_assets = set(valid["asset_id"].astype(str).unique())
    metric_gate_passed = bool(
        scores[selected]["rmse"] < baseline_metrics["rmse"]
        and scores[selected]["mae"] < baseline_metrics["mae"]
    )
    asset_disjoint = train_assets.isdisjoint(validation_assets)
    target_excluded_from_features = "rul" not in features
    promotion_gate = {
        "version": MODEL_PROMOTION_GATE_VERSION,
        "passed": bool(
            metric_gate_passed
            and len(validation_assets) >= MIN_EXTERNAL_VALIDATION_ASSETS
            and asset_disjoint
            and target_excluded_from_features
        ),
        "promotion_allowed": bool(
            metric_gate_passed
            and len(validation_assets) >= MIN_EXTERNAL_VALIDATION_ASSETS
            and asset_disjoint
            and target_excluded_from_features
            and resolved_evidence_class != "SYNTHETIC_TEST_ONLY"
        ),
        "rule": (
            "selected model must beat the recorded asset-level baseline on both RMSE and MAE; "
            "the untouched validation set must contain at least two independent asset trajectories; "
            "promotion still requires an explicit human approval"
        ),
        "metric_comparison": {
            "rmse_beats_baseline": scores[selected]["rmse"] < baseline_metrics["rmse"],
            "mae_beats_baseline": scores[selected]["mae"] < baseline_metrics["mae"],
            "selected_rmse": scores[selected]["rmse"],
            "baseline_rmse": baseline_metrics["rmse"],
            "selected_mae": scores[selected]["mae"],
            "baseline_mae": baseline_metrics["mae"],
        },
        "validation_design": "asset-level holdout; complete asset trajectories are held out",
        "train_asset_count": len(train_assets),
        "validation_asset_count": len(validation_assets),
        "minimum_validation_assets": MIN_EXTERNAL_VALIDATION_ASSETS,
        "asset_disjoint": asset_disjoint,
        "target_excluded_from_features": target_excluded_from_features,
        "claim_boundary": (
            "This is a dataset-specific validation gate. It does not transfer benchmark performance "
            "to unrelated equipment and does not certify field safety or production performance."
            + (
                " Synthetic fixture results are test-only and cannot be promoted to production."
                if resolved_evidence_class == "SYNTHETIC_TEST_ONLY"
                else ""
            )
        ),
    }
    final_model = clone(candidates[selected]).fit(feat[features], feat["rul"])

    signature = sha256(
        (
            dataset_id
            + selected
            + "|".join(features)
            + f"|gate-v{MODEL_PROMOTION_GATE_VERSION}|evidence={resolved_evidence_class}"
        ).encode()
    ).hexdigest()[:10]
    model_id = f"rul-{dataset_id[:28]}-{signature}"
    output = Path(output_root) / model_id
    output.mkdir(parents=True, exist_ok=True)
    joblib.dump(final_model, output / "model.joblib")
    manifest = {
        "model_id": model_id,
        "dataset_id": dataset_id,
        "task": "remaining_useful_life_regression",
        "created_at": datetime.now(UTC).isoformat(),
        "selected_model": selected,
        "features": features,
        "validation_assets": sorted(val_assets),
        "metrics": scores[selected],
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": scores,
        "residual_interval_90": width,
        "interval_coverage_90": coverage,
        "validation_design": "asset-level holdout; complete asset trajectories are held out",
        "promotion_gate": promotion_gate,
        "evidence_class": resolved_evidence_class,
        "promotion_allowed": promotion_gate["promotion_allowed"],
        "limitations": [
            "Validation applies only to the ingested dataset and split recorded here.",
            "It does not inherit NASA C-MAPSS benchmark claims.",
            "Residual interval is empirical holdout dispersion, not a formal conformal guarantee.",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    catalog.put_model(model_id, dataset_id, manifest)
    ModelRegistry(output_root).register(model_id, actor="training-service")
    return ExternalRULTrainingResult(
        model_id=model_id,
        dataset_id=dataset_id,
        selected_model=selected,
        features=features,
        validation_assets=sorted(val_assets),
        metrics=scores[selected],
        baseline_metrics=baseline_metrics,
        candidate_metrics=scores,
        residual_interval_90=width,
        interval_coverage_90=coverage,
        promotion_gate=promotion_gate,
        evidence_class=resolved_evidence_class,
    )


def predict_external_rul(
    telemetry: pd.DataFrame,
    model_id: str,
    model_root: str | Path = "data/external_models",
) -> list[dict]:
    root = Path(model_root) / model_id
    if not root.exists():
        raise ValueError(f"External model not found: {model_id}")
    require_approved = os.getenv("PDM_REQUIRE_APPROVED_MODELS", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if require_approved:
        registry_entry = ModelRegistry(model_root).get(model_id)
        if registry_entry is None or registry_entry.get("stage") != "approved":
            raise ValueError("Model is not approved for production scoring")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    model = joblib.load(root / "model.joblib")
    # Fake target only to reuse feature engineering; never supplied to the model.
    frame = telemetry.copy()
    if "rul" not in frame:
        frame["rul"] = 0.0
    feat, _ = engineer_generic_features(frame)
    missing = [c for c in manifest["features"] if c not in feat]
    if missing:
        raise ValueError(f"Dataset is incompatible with model feature contract; missing {missing[:12]}")
    axis = "cycle" if "cycle" in feat else "timestamp"
    latest = feat.sort_values(["asset_id", axis]).groupby("asset_id").tail(1).copy()
    pred = np.maximum(0.0, model.predict(latest[manifest["features"]]))
    out = []
    for row, value in zip(latest.itertuples(), pred):
        item = {
            "asset_id": str(row.asset_id),
            "predicted_rul": float(value),
            "rul_low_90": max(0.0, float(value) - float(manifest["residual_interval_90"])),
            "rul_high_90": float(value) + float(manifest["residual_interval_90"]),
            "model_id": model_id,
            "evidence_class": "PREDICTED_FROM_USER_TRAINED_MODEL",
        }
        if axis == "cycle":
            item["cycle"] = float(row.cycle)
        else:
            item["timestamp"] = str(row.timestamp)
        out.append(item)
    return out


def score_fd001_compatible(telemetry: pd.DataFrame) -> list[dict]:
    """Score an accepted dataset only when it satisfies the bundled FD001 input contract.

    Asset identifiers are remapped internally because the trained model does not use identity as
    a feature. The original identifiers are restored in the returned evidence.
    """
    from pdm_intelligence.data.cmapss import SENSOR_COLUMNS, SETTING_COLUMNS
    from pdm_intelligence.models.runtime import score_trajectory

    required = ["cycle", *SETTING_COLUMNS, *SENSOR_COLUMNS]
    missing = [c for c in required if c not in telemetry.columns]
    if missing:
        raise ValueError(f"Dataset is not compatible with bundled FD001 model; missing {missing}")
    ids = sorted(telemetry["asset_id"].astype(str).unique())
    forward = {asset: i + 1 for i, asset in enumerate(ids)}
    reverse = {i + 1: asset for i, asset in enumerate(ids)}
    frame = telemetry.copy()
    frame["unit_id"] = frame["asset_id"].astype(str).map(forward)
    records = frame[["unit_id", *required]].to_dict("records")
    scored = score_trajectory(records)
    for item in scored:
        item["asset_id"] = reverse[int(item.pop("unit_id"))]
        item["model_id"] = "bundled_fd001_release"
        item["evidence_class"] = "PREDICTED_WITH_SCHEMA_COMPATIBLE_FD001_MODEL"
        item["validation_boundary"] = "Model validation remains NASA C-MAPSS FD001 benchmark validation."
    return scored
