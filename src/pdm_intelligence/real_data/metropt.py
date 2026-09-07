from __future__ import annotations

import json
import math
import re
import shutil
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

METROPT_DATASET_ID = "metropt-3-uci-791"
METROPT_SOURCE_URL = "https://archive.ics.uci.edu/static/public/791/metropt%2B3%2Bdataset.zip"
METROPT_DOI = "10.24432/C5VW3R"
METROPT_LICENSE = "CC BY 4.0"
METROPT_ARCHIVE_REFERENCE_SHA256 = "aab991a970e58210de853bb8078ce0e63abb4d9412fdc5c79792dae3d8e1721a"
METROPT_ARCHIVE_MEMBER = "MetroPT3(AirCompressor).csv"
METROPT_EXPECTED_ROWS = 1_516_948
METROPT_EXPECTED_START = pd.Timestamp("2020-02-01 00:00:00")
METROPT_EXPECTED_END = pd.Timestamp("2020-09-01 03:59:50")
MINIMUM_INDEPENDENT_EVENTS_PER_EVALUATION = 2
METROPT_MODEL_QUALITY_GATE_NAME = "chronological_ap_roc_calibration_and_independent_event_coverage"
METROPT_RUNTIME_INTEGRITY_SCHEMA_VERSION = 1
METROPT_RUNTIME_DIGEST_FILENAME = "metropt_runtime.sha256"
METROPT_MODEL_BOOTSTRAP_ITERATIONS = 200
METROPT_MODEL_CONFIDENCE_LEVEL = 0.95
METROPT_BOOTSTRAP_BLOCK_HOURS = 24
METROPT_MINIMUM_BOOTSTRAP_GROUPS = 2

ANALOG_COLUMNS = [
    "TP2",
    "TP3",
    "H1",
    "DV_pressure",
    "Reservoirs",
    "Oil_temperature",
    "Motor_current",
]
DIGITAL_COLUMNS = [
    "COMP",
    "DV_eletric",
    "Towers",
    "MPG",
    "LPS",
    "Pressure_switch",
    "Oil_level",
    "Caudal_impulses",
]
SENSOR_COLUMNS = ANALOG_COLUMNS + DIGITAL_COLUMNS
INDEX_ALIASES = ("index", "Unnamed: 0", "X")


@dataclass(frozen=True)
class FailureEvent:
    event_id: str
    start: str
    end: str
    failure_mode: str = "air_leak"
    severity: str = "high_stress"
    source: str = "UCI/company failure report"

    @property
    def start_ts(self) -> pd.Timestamp:
        return pd.Timestamp(self.start)

    @property
    def end_ts(self) -> pd.Timestamp:
        return pd.Timestamp(self.end)


# These are the four company-reported air-leak windows published with UCI dataset 791.
METROPT_FAILURE_EVENTS = (
    FailureEvent("F01", "2020-04-18 00:00:00", "2020-04-18 23:59:00"),
    FailureEvent("F02", "2020-05-29 23:30:00", "2020-05-30 06:00:00"),
    FailureEvent("F03", "2020-06-05 10:00:00", "2020-06-07 14:30:00"),
    FailureEvent("F04", "2020-07-15 14:30:00", "2020-07-15 19:00:00"),
)


@dataclass(frozen=True)
class RawValidation:
    row_count: int
    columns: tuple[str, ...]
    first_timestamp: str
    last_timestamp: str
    missing_values: int
    index_column: str
    source_sha256: str
    file_size_bytes: int
    actual_sampling_median_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _integrity_descriptor(path: Path, relative_path: str) -> dict[str, Any]:
    """Describe a generated artifact without embedding machine-specific paths."""

    return {
        "relative_path": relative_path,
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def verify_metropt_runtime_integrity(root: str | Path, runtime: dict[str, Any]) -> dict[str, Any]:
    """Verify the runtime evidence file and artifacts bound to it."""

    manifest = runtime.get("integrity")
    if not isinstance(manifest, dict):
        return {
            "status": "MISSING",
            "passed": False,
            "errors": ["integrity_manifest_missing"],
            "checks": {},
        }
    if manifest.get("schema_version") != METROPT_RUNTIME_INTEGRITY_SCHEMA_VERSION:
        return {
            "status": "FAIL",
            "passed": False,
            "errors": ["integrity_manifest_schema_unsupported"],
            "checks": {},
        }

    root_path = Path(root).resolve()
    runtime_path = root_path / "derived" / "metropt_runtime.json"
    digest_path = root_path / "derived" / METROPT_RUNTIME_DIGEST_FILENAME
    descriptors: dict[str, Any] = {"feature_store": manifest.get("feature_store")}
    model_artifacts = manifest.get("model_artifacts")
    if isinstance(model_artifacts, dict):
        descriptors.update({f"model:{name}": value for name, value in model_artifacts.items()})
    errors: list[str] = []
    checks: dict[str, Any] = {}
    if not runtime_path.is_file():
        errors.append("runtime_manifest_missing")
    elif not digest_path.is_file():
        errors.append("runtime_manifest_digest_missing")
    else:
        actual_runtime_sha256 = _sha256_file(runtime_path)
        supplied_digest = digest_path.read_text(encoding="utf-8").strip()
        runtime_check = {
            "relative_path": "derived/metropt_runtime.json",
            "digest_path": f"derived/{METROPT_RUNTIME_DIGEST_FILENAME}",
            "expected_sha256": supplied_digest,
            "actual_sha256": actual_runtime_sha256,
            "size_bytes": runtime_path.stat().st_size,
        }
        checks["runtime_manifest"] = runtime_check
        if not re.fullmatch(r"[0-9a-fA-F]{64}", supplied_digest):
            errors.append("runtime_manifest_digest_invalid")
        elif supplied_digest.lower() != actual_runtime_sha256:
            errors.append("runtime_manifest:sha256_mismatch")
    for name, descriptor in descriptors.items():
        if not isinstance(descriptor, dict):
            errors.append(f"{name}:descriptor_missing")
            continue
        relative_path = descriptor.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path or Path(relative_path).is_absolute():
            errors.append(f"{name}:relative_path_invalid")
            continue
        candidate = (root_path / relative_path).resolve()
        if not candidate.is_relative_to(root_path):
            errors.append(f"{name}:path_escapes_runtime_root")
            continue
        if not candidate.is_file():
            errors.append(f"{name}:artifact_missing")
            continue
        actual_sha256 = _sha256_file(candidate)
        actual_size = candidate.stat().st_size
        expected_sha256 = descriptor.get("sha256")
        expected_size = descriptor.get("size_bytes")
        check = {
            "relative_path": relative_path,
            "expected_sha256": expected_sha256,
            "actual_sha256": actual_sha256,
            "expected_size_bytes": expected_size,
            "actual_size_bytes": actual_size,
        }
        checks[name] = check
        if actual_sha256 != expected_sha256:
            errors.append(f"{name}:sha256_mismatch")
        if expected_size is not None and actual_size != expected_size:
            errors.append(f"{name}:size_mismatch")
    return {
        "status": "PASS" if not errors else "FAIL",
        "passed": not errors,
        "errors": errors,
        "checks": checks,
    }


def download_metropt3(target_dir: str | Path, *, timeout_seconds: int = 600) -> dict[str, str]:
    """Acquire the public UCI archive without committing it to the repository."""
    root = Path(target_dir)
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "metropt-3-dataset.zip"
    csv_path = root / METROPT_ARCHIVE_MEMBER
    if csv_path.exists():
        return {
            "archive_path": str(archive) if archive.exists() else "",
            "csv_path": str(csv_path),
            "status": "EXISTING_RAW_DATA",
        }

    request = Request(METROPT_SOURCE_URL, headers={"User-Agent": "PDM-Reliability-Observatory/1.1"})
    tmp = archive.with_suffix(".zip.part")
    if not archive.exists():
        print(f"Downloading MetroPT-3 from UCI: {METROPT_SOURCE_URL}", flush=True)
        downloaded = 0
        next_report = 10 * 1024 * 1024
        with urlopen(request, timeout=timeout_seconds) as response, tmp.open("wb") as out:
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else None
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
                downloaded += len(block)
                if downloaded >= next_report:
                    if total:
                        print(f"  downloaded {downloaded / 1024**2:.0f}/{total / 1024**2:.0f} MB", flush=True)
                    else:
                        print(f"  downloaded {downloaded / 1024**2:.0f} MB", flush=True)
                    next_report += 10 * 1024 * 1024
        tmp.replace(archive)
    else:
        print(f"Reusing existing MetroPT-3 archive: {archive}", flush=True)
    archive_sha = _sha256_file(archive)
    # UCI does not publish a digest on the dataset page. We record the current known archive
    # checksum as reproducibility evidence, but schema/content validation remains authoritative.
    print("Extracting MetroPT3(AirCompressor).csv ...", flush=True)
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        if METROPT_ARCHIVE_MEMBER not in names:
            raise ValueError(f"UCI archive does not contain {METROPT_ARCHIVE_MEMBER}")
        with zf.open(METROPT_ARCHIVE_MEMBER) as src, csv_path.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    return {
        "archive_path": str(archive),
        "archive_sha256": archive_sha,
        "archive_reference_sha256": METROPT_ARCHIVE_REFERENCE_SHA256,
        "archive_reference_match": str(archive_sha == METROPT_ARCHIVE_REFERENCE_SHA256).lower(),
        "csv_path": str(csv_path),
        "status": "DOWNLOADED_FROM_UCI",
    }


def _normalize_header(columns: Iterable[str]) -> tuple[list[str], str]:
    cols = [str(x).strip() for x in columns]
    index_col = next((x for x in INDEX_ALIASES if x in cols), "")
    required = {"timestamp", *SENSOR_COLUMNS}
    missing = sorted(required - set(cols))
    if missing:
        raise ValueError(f"MetroPT-3 schema missing required columns: {missing}")
    if not index_col:
        raise ValueError(f"MetroPT-3 source index column not found; expected one of {INDEX_ALIASES}")
    return cols, index_col


def validate_metropt3_csv(
    csv_path: str | Path,
    *,
    strict_full_dataset: bool = True,
    chunksize: int = 250_000,
) -> RawValidation:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(path)
    header = pd.read_csv(path, nrows=0)
    columns, index_col = _normalize_header(header.columns)
    row_count = 0
    missing_values = 0
    first_ts: pd.Timestamp | None = None
    last_ts: pd.Timestamp | None = None
    sample_deltas: list[float] = []
    previous_ts: pd.Timestamp | None = None
    usecols = [index_col, "timestamp", *SENSOR_COLUMNS]
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunksize):
        ts = pd.to_datetime(chunk["timestamp"], errors="coerce")
        if ts.isna().any():
            raise ValueError("MetroPT-3 contains unparseable timestamps")
        numeric = chunk[[index_col, *SENSOR_COLUMNS]].apply(pd.to_numeric, errors="coerce")
        missing_values += int(numeric.isna().sum().sum())
        if missing_values:
            raise ValueError("MetroPT-3 contains missing/non-numeric values in required source fields")
        if not ts.is_monotonic_increasing:
            raise ValueError("MetroPT-3 timestamps are not monotonic within a source chunk")
        if previous_ts is not None and ts.iloc[0] < previous_ts:
            raise ValueError("MetroPT-3 timestamps are not monotonic across source chunks")
        if first_ts is None:
            first_ts = ts.iloc[0]
        last_ts = ts.iloc[-1]
        if len(sample_deltas) < 5_000:
            values = ts.head(min(len(ts), 5_001)).astype("int64").to_numpy()
            if previous_ts is not None:
                values = np.concatenate([[previous_ts.value], values])
            diffs = np.diff(values) / 1e9
            sample_deltas.extend([float(x) for x in diffs if x > 0][: 5_000 - len(sample_deltas)])
        previous_ts = ts.iloc[-1]
        row_count += len(chunk)
    assert first_ts is not None and last_ts is not None
    if strict_full_dataset:
        if row_count != METROPT_EXPECTED_ROWS:
            raise ValueError(f"MetroPT-3 row count mismatch: expected {METROPT_EXPECTED_ROWS}, got {row_count}")
        if first_ts != METROPT_EXPECTED_START or last_ts != METROPT_EXPECTED_END:
            raise ValueError(
                "MetroPT-3 timestamp range mismatch: "
                f"expected {METROPT_EXPECTED_START}..{METROPT_EXPECTED_END}, got {first_ts}..{last_ts}"
            )
    return RawValidation(
        row_count=row_count,
        columns=tuple(columns),
        first_timestamp=str(first_ts),
        last_timestamp=str(last_ts),
        missing_values=missing_values,
        index_column=index_col,
        source_sha256=_sha256_file(path),
        file_size_bytes=path.stat().st_size,
        actual_sampling_median_seconds=float(np.median(sample_deltas)) if sample_deltas else None,
    )


def _partial_aggregate(chunk: pd.DataFrame, index_col: str, bucket_minutes: int) -> pd.DataFrame:
    chunk = chunk.copy()
    chunk["timestamp"] = pd.to_datetime(chunk["timestamp"], errors="raise")
    for col in SENSOR_COLUMNS:
        chunk[col] = pd.to_numeric(chunk[col], errors="raise")
    chunk["bucket"] = chunk["timestamp"].dt.floor(f"{int(bucket_minutes)}min")
    for col in ANALOG_COLUMNS:
        chunk[f"{col}__sq"] = chunk[col].astype(float) ** 2
    grouped = chunk.groupby("bucket", sort=True)
    out = pd.DataFrame(index=grouped.size().index)
    out["row_count"] = grouped.size().astype(int)
    for col in ANALOG_COLUMNS:
        out[f"{col}__sum"] = grouped[col].sum()
        out[f"{col}__sumsq"] = grouped[f"{col}__sq"].sum()
        out[f"{col}__min"] = grouped[col].min()
        out[f"{col}__max"] = grouped[col].max()
    for col in DIGITAL_COLUMNS:
        out[f"{col}__sum"] = grouped[col].sum()
    out["source_index_min"] = grouped[index_col].min()
    out["source_index_max"] = grouped[index_col].max()
    return out.reset_index()


def build_metropt_feature_store(
    csv_path: str | Path,
    output_path: str | Path,
    *,
    bucket_minutes: int = 10,
    chunksize: int = 200_000,
) -> pd.DataFrame:
    """Stream the 1.5M-row source into a compact chronological engineering feature store."""
    path = Path(csv_path)
    header = pd.read_csv(path, nrows=0)
    _, index_col = _normalize_header(header.columns)
    usecols = [index_col, "timestamp", *SENSOR_COLUMNS]
    partials: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=chunksize):
        partials.append(_partial_aggregate(chunk, index_col, bucket_minutes))
    partial = pd.concat(partials, ignore_index=True)
    # Recombine chunk-boundary buckets exactly using sufficient statistics.
    agg_spec: dict[str, str] = {"row_count": "sum", "source_index_min": "min", "source_index_max": "max"}
    for col in ANALOG_COLUMNS:
        agg_spec[f"{col}__sum"] = "sum"
        agg_spec[f"{col}__sumsq"] = "sum"
        agg_spec[f"{col}__min"] = "min"
        agg_spec[f"{col}__max"] = "max"
    for col in DIGITAL_COLUMNS:
        agg_spec[f"{col}__sum"] = "sum"
    combined = partial.groupby("bucket", as_index=False, sort=True).agg(agg_spec)
    feature = pd.DataFrame({"timestamp": pd.to_datetime(combined["bucket"]), "row_count": combined["row_count"].astype(int)})
    feature["source_index_min"] = combined["source_index_min"]
    feature["source_index_max"] = combined["source_index_max"]
    count = combined["row_count"].to_numpy(dtype=float)
    for col in ANALOG_COLUMNS:
        sums = combined[f"{col}__sum"].to_numpy(dtype=float)
        sumsq = combined[f"{col}__sumsq"].to_numpy(dtype=float)
        mean = sums / np.maximum(count, 1.0)
        variance = np.maximum(0.0, (sumsq - sums * sums / np.maximum(count, 1.0)) / np.maximum(count - 1.0, 1.0))
        feature[f"{col}_mean"] = mean
        feature[f"{col}_std"] = np.sqrt(variance)
        feature[f"{col}_min"] = combined[f"{col}__min"].to_numpy(dtype=float)
        feature[f"{col}_max"] = combined[f"{col}__max"].to_numpy(dtype=float)
    for col in DIGITAL_COLUMNS:
        feature[f"{col}_duty"] = combined[f"{col}__sum"].to_numpy(dtype=float) / np.maximum(count, 1.0)
    feature["pressure_drop_mean"] = feature["TP3_mean"] - feature["Reservoirs_mean"]
    feature["compressor_to_panel_gap"] = feature["TP2_mean"] - feature["TP3_mean"]
    feature["motor_load_proxy"] = feature["Motor_current_mean"] * (1.0 - feature["COMP_duty"])
    # Past-only temporal features give the real-data candidate model degradation context
    # without peeking beyond the decision timestamp. With 10-minute buckets, 6 and 36
    # observations correspond to approximately one and six hours.
    temporal_bases = [f"{col}_mean" for col in ANALOG_COLUMNS] + [
        "pressure_drop_mean",
        "compressor_to_panel_gap",
        "motor_load_proxy",
    ]
    for col in temporal_bases:
        feature[f"{col}__delta1"] = feature[col].diff().fillna(0.0)
        feature[f"{col}__roll6_mean"] = feature[col].rolling(6, min_periods=1).mean()
        feature[f"{col}__roll6_std"] = feature[col].rolling(6, min_periods=2).std().fillna(0.0)
        feature[f"{col}__roll36_mean"] = feature[col].rolling(36, min_periods=1).mean()
        feature[f"{col}__roll36_std"] = feature[col].rolling(36, min_periods=2).std().fillna(0.0)
    feature["failure_active"] = 0
    feature["failure_within_24h"] = 0
    feature["failure_within_6h"] = 0
    feature["failure_event_id"] = ""
    feature["hours_to_next_failure"] = np.nan
    ts = feature["timestamp"]
    for event in METROPT_FAILURE_EVENTS:
        active = (ts >= event.start_ts) & (ts <= event.end_ts)
        pre24 = (ts >= event.start_ts - pd.Timedelta(hours=24)) & (ts < event.start_ts)
        pre6 = (ts >= event.start_ts - pd.Timedelta(hours=6)) & (ts < event.start_ts)
        feature.loc[active, "failure_active"] = 1
        feature.loc[pre24, "failure_within_24h"] = 1
        feature.loc[pre6, "failure_within_6h"] = 1
        feature.loc[active | pre24, "failure_event_id"] = event.event_id
        delta = (event.start_ts - ts).dt.total_seconds() / 3600.0
        eligible = delta >= 0
        current = feature["hours_to_next_failure"]
        replacement = eligible & (current.isna() | (delta < current))
        feature.loc[replacement, "hours_to_next_failure"] = delta[replacement]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    feature.to_csv(output, index=False, compression="gzip")
    return feature


def _model_features(frame: pd.DataFrame) -> list[str]:
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
    return [c for c in frame.columns if c not in blocked and pd.api.types.is_numeric_dtype(frame[c])]


def _baseline_features(frame: pd.DataFrame) -> list[str]:
    wanted = [f"{c}_mean" for c in ANALOG_COLUMNS]
    return [c for c in wanted if c in frame.columns]


def _prob_metrics(y_true: np.ndarray, prob: np.ndarray, threshold: float) -> dict[str, float]:
    pred = prob >= threshold
    prevalence = float(np.mean(y_true))
    out = {
        "prevalence": prevalence,
        "prevalence_brier": float(prevalence * (1.0 - prevalence)),
        "average_precision": float(average_precision_score(y_true, prob)) if len(np.unique(y_true)) > 1 else prevalence,
        "brier": float(brier_score_loss(y_true, prob)),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "threshold": float(threshold),
    }
    out["roc_auc"] = float(roc_auc_score(y_true, prob)) if len(np.unique(y_true)) > 1 else float("nan")
    return out


def _bootstrap_metric_intervals(
    y_true: np.ndarray,
    prob: np.ndarray,
    *,
    groups: np.ndarray,
    seed: int,
    iterations: int = METROPT_MODEL_BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    """Estimate clustered uncertainty bounds without changing the holdout.

    Positive rows are resampled by published failure event, while negative rows
    are resampled by contiguous background blocks. This avoids treating highly
    correlated telemetry rows as independent observations.
    """

    if len(groups) != len(y_true):
        raise ValueError("Bootstrap groups must have the same length as labels")
    labels = np.asarray(groups, dtype=str)
    positive_groups = np.unique(labels[y_true == 1])
    negative_groups = np.unique(labels[y_true == 0])
    if not len(positive_groups) or not len(negative_groups):
        return {
            "iterations": 0,
            "confidence_level": METROPT_MODEL_CONFIDENCE_LEVEL,
            "status": "INSUFFICIENT_CLASSES",
            "bootstrap_unit": "failure_event_and_background_block",
            "positive_group_count": len(positive_groups),
            "negative_group_count": len(negative_groups),
        }
    if min(len(positive_groups), len(negative_groups)) < METROPT_MINIMUM_BOOTSTRAP_GROUPS:
        return {
            "iterations": 0,
            "confidence_level": METROPT_MODEL_CONFIDENCE_LEVEL,
            "status": "INSUFFICIENT_INDEPENDENT_GROUPS",
            "bootstrap_unit": "failure_event_and_background_block",
            "positive_group_count": len(positive_groups),
            "negative_group_count": len(negative_groups),
        }
    rng = np.random.default_rng(seed)
    positive_indices = {
        group: np.flatnonzero((y_true == 1) & (labels == group))
        for group in positive_groups
    }
    negative_indices = {
        group: np.flatnonzero((y_true == 0) & (labels == group))
        for group in negative_groups
    }
    values = {"average_precision": [], "roc_auc": [], "brier": []}
    for _ in range(iterations):
        sampled_positive_groups = rng.choice(
            positive_groups, size=len(positive_groups), replace=True
        )
        sampled_negative_groups = rng.choice(
            negative_groups, size=len(negative_groups), replace=True
        )
        sample = np.concatenate(
            [
                np.concatenate([positive_indices[group] for group in sampled_positive_groups]),
                np.concatenate([negative_indices[group] for group in sampled_negative_groups]),
            ]
        )
        metrics = _prob_metrics(y_true[sample], prob[sample], 0.5)
        values["average_precision"].append(metrics["average_precision"])
        values["roc_auc"].append(metrics["roc_auc"])
        values["brier"].append(metrics["brier"])
    tail = (1.0 - METROPT_MODEL_CONFIDENCE_LEVEL) / 2.0
    return {
        "iterations": iterations,
        "confidence_level": METROPT_MODEL_CONFIDENCE_LEVEL,
        "status": "PASS",
        "bootstrap_unit": "failure_event_and_background_block",
        "positive_group_count": len(positive_groups),
        "negative_group_count": len(negative_groups),
        "background_block_hours": METROPT_BOOTSTRAP_BLOCK_HOURS,
        **{
            name: {
                "lower": float(np.quantile(samples, tail)),
                "upper": float(np.quantile(samples, 1.0 - tail)),
            }
            for name, samples in values.items()
        },
    }


def _supervised_gate_components(
    metrics: dict[str, float], intervals: dict[str, Any]
) -> dict[str, bool]:
    """Require point estimates and uncertainty bounds on each split."""

    return {
        "average_precision_above_prevalence": bool(metrics["average_precision"] > metrics["prevalence"]),
        "roc_auc_above_chance": bool(np.isfinite(metrics["roc_auc"]) and metrics["roc_auc"] > 0.5),
        "brier_better_than_prevalence_baseline": bool(metrics["brier"] < metrics["prevalence_brier"]),
        "confidence_bounds_support_gate": bool(
            intervals.get("status") == "PASS"
            and intervals["average_precision"]["lower"] > metrics["prevalence"]
            and intervals["roc_auc"]["lower"] > 0.5
            and intervals["brier"]["upper"] < metrics["prevalence_brier"]
        ),
    }


def _ranking_metrics(y_true: np.ndarray, score: np.ndarray) -> dict[str, float]:
    """Measure ranking quality for non-probabilistic evidence such as anomaly scores."""

    prevalence = float(np.mean(y_true)) if len(y_true) else 0.0
    out = {
        "prevalence": prevalence,
        "average_precision": (
            float(average_precision_score(y_true, score))
            if len(y_true) and len(np.unique(y_true)) > 1
            else prevalence
        ),
    }
    out["roc_auc"] = (
        float(roc_auc_score(y_true, score))
        if len(y_true) and len(np.unique(y_true)) > 1
        else float("nan")
    )
    return out


def _choose_threshold(y_true: np.ndarray, prob: np.ndarray) -> float:
    candidates = np.unique(np.concatenate([np.linspace(0.05, 0.95, 37), prob]))
    best = (float("-inf"), 0.5)
    for threshold in candidates:
        score = f1_score(y_true, prob >= threshold, zero_division=0)
        # Prefer the higher threshold when F1 ties to reduce alert flooding.
        key = (float(score), float(threshold))
        best = max(best, key)
    return float(best[1])


def _sample_weights(y: np.ndarray) -> np.ndarray:
    positive = int(y.sum())
    negative = int(len(y) - positive)
    if positive == 0 or negative == 0:
        return np.ones(len(y), dtype=float)
    pos_weight = negative / positive
    return np.where(y == 1, pos_weight, 1.0).astype(float)


def _partition_summary(frame: pd.DataFrame, target_col: str) -> dict[str, float | int]:
    """Return label coverage without exposing telemetry rows."""

    positive_count = int(frame[target_col].astype(int).sum())
    row_count = len(frame)
    return {
        "row_count": row_count,
        "positive_count": positive_count,
        "positive_rate": float(positive_count / row_count) if row_count else 0.0,
    }


def _event_coverage(frame: pd.DataFrame) -> dict[str, Any]:
    """Summarize independent published failure windows represented by a split."""

    event_ids = sorted(
        {
            str(value).strip()
            for value in frame["failure_event_id"].dropna().astype(str)
            if str(value).strip()
        }
    )
    return {"event_ids": event_ids, "event_count": len(event_ids)}


def _bootstrap_group_labels(frame: pd.DataFrame, target_col: str) -> np.ndarray:
    """Build independent resampling units for a chronological evaluation split."""

    timestamps = pd.to_datetime(frame["timestamp"], errors="raise")
    event_ids = frame["failure_event_id"].fillna("").astype(str).str.strip()
    target = frame[target_col].astype(int).to_numpy()
    background_blocks = timestamps.dt.floor(f"{METROPT_BOOTSTRAP_BLOCK_HOURS}h").astype(str)
    labels = np.where(
        target == 1,
        "failure_event:" + event_ids.replace("", "unknown"),
        "background_block:" + background_blocks,
    )
    return np.asarray(labels, dtype=str)


def train_metropt_models(
    feature_frame: pd.DataFrame,
    model_root: str | Path,
    *,
    seed: int = 731,
    horizon_hours: int = 24,
) -> dict[str, Any]:
    frame = feature_frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="raise")
    target_col = "failure_within_24h" if horizon_hours == 24 else "failure_within_6h"
    if target_col not in frame:
        raise ValueError(f"Unsupported failure horizon {horizon_hours}h")
    usable = frame[frame["failure_active"] == 0].copy()
    fit_end = pd.Timestamp("2020-06-01 00:00:00")
    validation_end = pd.Timestamp("2020-07-01 00:00:00")
    train = usable[usable["timestamp"] < fit_end]
    validation = usable[(usable["timestamp"] >= fit_end) & (usable["timestamp"] < validation_end)]
    holdout = usable[usable["timestamp"] >= validation_end]
    if min(len(train), len(validation), len(holdout)) == 0:
        raise ValueError("MetroPT chronological train/validation/holdout partitions are incomplete")
    feature_cols = _model_features(usable)
    baseline_cols = _baseline_features(usable)
    y_train = train[target_col].astype(int).to_numpy()
    y_val = validation[target_col].astype(int).to_numpy()
    y_hold = holdout[target_col].astype(int).to_numpy()
    if y_train.sum() == 0 or y_val.sum() == 0 or y_hold.sum() == 0:
        raise ValueError("MetroPT failure-horizon partitions must each contain positive pre-failure observations")
    baseline = Pipeline([
        ("scale", StandardScaler()),
        ("model", LogisticRegression(max_iter=800, class_weight="balanced", random_state=seed)),
    ])
    baseline.fit(train[baseline_cols], y_train)
    val_base = baseline.predict_proba(validation[baseline_cols])[:, 1]
    baseline_threshold = _choose_threshold(y_val, val_base)
    baseline_validation = _prob_metrics(y_val, val_base, baseline_threshold)

    candidate = RandomForestClassifier(
        n_estimators=220,
        max_depth=12,
        min_samples_leaf=4,
        max_features="sqrt",
        class_weight="balanced_subsample",
        random_state=seed,
        n_jobs=1,
    )
    candidate.fit(train[feature_cols], y_train)
    val_candidate = candidate.predict_proba(validation[feature_cols])[:, 1]
    candidate_threshold = _choose_threshold(y_val, val_candidate)
    candidate_validation = _prob_metrics(y_val, val_candidate, candidate_threshold)
    boosted = HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=180, max_leaf_nodes=15,
        min_samples_leaf=40, l2_regularization=1.0, random_state=seed,
    )
    boosted.fit(train[feature_cols], y_train, sample_weight=_sample_weights(y_train))
    val_boosted = boosted.predict_proba(validation[feature_cols])[:, 1]
    boosted_threshold = _choose_threshold(y_val, val_boosted)
    boosted_validation = _prob_metrics(y_val, val_boosted, boosted_threshold)
    candidates = [
        ("logistic_baseline", baseline, baseline_cols, baseline_threshold, baseline_validation),
        ("random_forest", candidate, feature_cols, candidate_threshold, candidate_validation),
        ("hist_gradient_boosting", boosted, feature_cols, boosted_threshold, boosted_validation),
    ]
    eligible = [x for x in candidates if x[4]["brier"] <= baseline_validation["brier"] * 1.05]
    selected_name, selected_model, selected_features, selected_threshold, selected_validation = max(
        eligible or candidates, key=lambda x: (x[4]["average_precision"], -x[4]["brier"])
    )
    promote_candidate = selected_name != "logistic_baseline"
    selected_validation_prob = selected_model.predict_proba(validation[selected_features])[:, 1]

    # Refit the selected model on everything before the untouched July/August holdout.
    development = usable[usable["timestamp"] < validation_end]
    y_dev = development[target_col].astype(int).to_numpy()
    selected_model.fit(development[selected_features], y_dev)
    hold_prob = selected_model.predict_proba(holdout[selected_features])[:, 1]
    hold_metrics = _prob_metrics(y_hold, hold_prob, selected_threshold)
    validation_intervals = _bootstrap_metric_intervals(
        y_val,
        selected_validation_prob,
        groups=_bootstrap_group_labels(validation, target_col),
        seed=seed + 17,
    )
    holdout_intervals = _bootstrap_metric_intervals(
        y_hold,
        hold_prob,
        groups=_bootstrap_group_labels(holdout, target_col),
        seed=seed + 19,
    )
    validation_gate_components = _supervised_gate_components(
        selected_validation, validation_intervals
    )
    holdout_gate_components = _supervised_gate_components(hold_metrics, holdout_intervals)
    validation_gate_passed = all(validation_gate_components.values())
    holdout_gate_passed = all(holdout_gate_components.values())
    train_event_coverage = _event_coverage(train)
    validation_event_coverage = _event_coverage(validation)
    holdout_event_coverage = _event_coverage(holdout)
    independent_event_coverage_passed = bool(
        validation_event_coverage["event_count"] >= MINIMUM_INDEPENDENT_EVENTS_PER_EVALUATION
        and holdout_event_coverage["event_count"] >= MINIMUM_INDEPENDENT_EVENTS_PER_EVALUATION
    )

    # Operational anomaly detector learns only the February reference period.
    feb = usable[(usable["timestamp"] >= METROPT_EXPECTED_START) & (usable["timestamp"] < pd.Timestamp("2020-03-01"))]
    anomaly_features = feature_cols
    scaler = StandardScaler()
    feb_scaled = scaler.fit_transform(feb[anomaly_features])
    anomaly_model = IsolationForest(n_estimators=180, contamination=0.01, random_state=seed, n_jobs=1)
    anomaly_model.fit(feb_scaled)
    baseline_scores = -anomaly_model.decision_function(feb_scaled)
    anomaly_threshold = float(np.quantile(baseline_scores, 0.99))
    all_scores = -anomaly_model.decision_function(scaler.transform(usable[anomaly_features]))
    score_by_time = pd.DataFrame({"timestamp": usable["timestamp"].to_numpy(), "anomaly_score": all_scores})
    validation_mask = (usable["timestamp"] >= fit_end) & (usable["timestamp"] < validation_end)
    holdout_mask = usable["timestamp"] >= validation_end
    anomaly_quality = {
        "validation": _ranking_metrics(
            usable.loc[validation_mask, target_col].astype(int).to_numpy(), all_scores[validation_mask.to_numpy()]
        ),
        "holdout": _ranking_metrics(
            usable.loc[holdout_mask, target_col].astype(int).to_numpy(), all_scores[holdout_mask.to_numpy()]
        ),
        "claim_boundary": (
            "Anomaly scores are reference-deviation rankings, not calibrated failure probabilities. "
            "They remain separate from the supervised failure-horizon quality gate."
        ),
    }
    event_detection = []
    for event in METROPT_FAILURE_EVENTS:
        pre = score_by_time[(score_by_time["timestamp"] >= event.start_ts - pd.Timedelta(hours=horizon_hours)) & (score_by_time["timestamp"] < event.start_ts)]
        active = score_by_time[(score_by_time["timestamp"] >= event.start_ts) & (score_by_time["timestamp"] <= event.end_ts)]
        event_detection.append({
            "event_id": event.event_id,
            "pre_failure_alert": bool((pre["anomaly_score"] >= anomaly_threshold).any()),
            "pre_failure_peak_score": float(pre["anomaly_score"].max()) if not pre.empty else None,
            "active_failure_peak_score": float(active["anomaly_score"].max()) if not active.empty else None,
        })

    root = Path(model_root)
    root.mkdir(parents=True, exist_ok=True)
    model_path = root / "metropt_failure_horizon.joblib"
    anomaly_path = root / "metropt_anomaly.joblib"
    joblib.dump({"model": selected_model, "features": selected_features, "threshold": selected_threshold}, model_path)
    joblib.dump({"scaler": scaler, "model": anomaly_model, "features": anomaly_features, "threshold": anomaly_threshold,
                 "baseline_scores": baseline_scores}, anomaly_path)

    # Persist a compact scored holdout timeline for the operational UI. This is derived evidence, not raw data.
    hold_scored = holdout[["timestamp", "failure_within_24h", "failure_within_6h", "failure_event_id", "hours_to_next_failure", *ANALOG_COLUMNS[:0]]].copy()
    hold_scored["failure_probability"] = hold_prob
    # Tree vote dispersion gives a model-ensemble uncertainty interval when RF is selected.
    if selected_name == "random_forest":
        hold_matrix = holdout[selected_features].to_numpy(dtype=float)
        votes = np.vstack([tree.predict_proba(hold_matrix)[:, 1] for tree in selected_model.estimators_])
        hold_scored["probability_p10"] = np.quantile(votes, 0.10, axis=0)
        hold_scored["probability_p90"] = np.quantile(votes, 0.90, axis=0)
    else:
        hold_scored["probability_p10"] = np.clip(hold_prob - 0.10, 0.0, 1.0)
        hold_scored["probability_p90"] = np.clip(hold_prob + 0.10, 0.0, 1.0)
    lookup = score_by_time.set_index("timestamp")["anomaly_score"]
    hold_scored["anomaly_score"] = hold_scored["timestamp"].map(lookup)
    compact = root / "metropt_holdout_scores.csv.gz"
    hold_scored.to_csv(compact, index=False, compression="gzip")

    return {
        "evidence_class": "VALIDATED_ON_REAL_OPERATIONAL_METROPT3_HOLDOUT",
        "dataset_id": METROPT_DATASET_ID,
        "horizon_hours": horizon_hours,
        "selected_model": selected_name,
        "selected_complex_candidate": bool(promote_candidate),
        "complex_model_promoted": bool(
            promote_candidate
            and validation_gate_passed
            and holdout_gate_passed
            and independent_event_coverage_passed
        ),
        "selection_rule": "select the highest-validation-AP candidate among logistic, RF and histogram gradient boosting, subject to Brier degradation <=5% versus logistic",
        "features": selected_features,
        "candidate_feature_count": len(feature_cols),
        "baseline_feature_count": len(baseline_cols),
        "partition": {
            "fit_before": str(fit_end),
            "validation": f"{fit_end} to {validation_end}",
            "holdout_from": str(validation_end),
            "shuffle": False,
            "train": _partition_summary(train, target_col),
            "validation_label_coverage": _partition_summary(validation, target_col),
            "holdout_label_coverage": _partition_summary(holdout, target_col),
            "train_event_coverage": train_event_coverage,
            "validation_event_coverage": validation_event_coverage,
            "holdout_event_coverage": holdout_event_coverage,
        },
        "validation_metrics": {
            "logistic_baseline": baseline_validation,
            "random_forest": candidate_validation,
            "hist_gradient_boosting": boosted_validation,
        },
        "validation_metric_intervals": validation_intervals,
        "holdout_metrics": hold_metrics,
        "holdout_metric_intervals": holdout_intervals,
        "quality_gate": {
            "name": METROPT_MODEL_QUALITY_GATE_NAME,
            "selected_model": selected_name,
            "validation_average_precision": selected_validation["average_precision"],
            "validation_prevalence": selected_validation["prevalence"],
            "validation_passed": validation_gate_passed,
            "validation_components": validation_gate_components,
            "validation_metric_intervals": validation_intervals,
            "holdout_passed": holdout_gate_passed,
            "holdout_components": holdout_gate_components,
            "holdout_metric_intervals": holdout_intervals,
            "event_coverage": {
                "minimum_independent_events_per_evaluation": MINIMUM_INDEPENDENT_EVENTS_PER_EVALUATION,
                "validation": validation_event_coverage,
                "holdout": holdout_event_coverage,
                "passed": independent_event_coverage_passed,
            },
            "passed": validation_gate_passed and holdout_gate_passed and independent_event_coverage_passed,
            "promotion_allowed": validation_gate_passed and holdout_gate_passed and independent_event_coverage_passed,
            "failure_reason": (
                ";".join(
                    reason
                    for reason, failed in (
                        (
                            "validation_average_precision_did_not_clear_validation_prevalence",
                            not validation_gate_components["average_precision_above_prevalence"],
                        ),
                        (
                            "future_holdout_average_precision_did_not_clear_holdout_prevalence",
                            not holdout_gate_components["average_precision_above_prevalence"],
                        ),
                        (
                            "validation_roc_auc_did_not_clear_chance",
                            not validation_gate_components["roc_auc_above_chance"],
                        ),
                        (
                            "future_holdout_roc_auc_did_not_clear_chance",
                            not holdout_gate_components["roc_auc_above_chance"],
                        ),
                        (
                            "validation_brier_did_not_beat_prevalence_baseline",
                            not validation_gate_components["brier_better_than_prevalence_baseline"],
                        ),
                        (
                            "future_holdout_brier_did_not_beat_prevalence_baseline",
                            not holdout_gate_components["brier_better_than_prevalence_baseline"],
                        ),
                        (
                            "validation_confidence_bounds_insufficient",
                            not validation_gate_components["confidence_bounds_support_gate"],
                        ),
                        (
                            "future_holdout_confidence_bounds_insufficient",
                            not holdout_gate_components["confidence_bounds_support_gate"],
                        ),
                        (
                            "independent_failure_event_coverage_insufficient",
                            not independent_event_coverage_passed,
                        ),
                    )
                    if failed
                )
                or None
            ),
            "claim_boundary": (
                "Both chronological validation and the untouched future-holdout gates must show average "
                "precision above prevalence, ROC-AUC above chance, and Brier loss better than a constant "
                "prevalence predictor, with event/block-clustered 95% bootstrap bounds supporting those comparisons "
                "and at least two independent published failure windows represented in each evaluation "
                "period before production promotion. Anomaly ranking evidence cannot substitute for this "
                "supervised failure-horizon gate."
            ),
        },
        "anomaly": {
            "model": "isolation_forest",
            "reference_period": "2020-02-01 to 2020-03-01",
            "threshold_quantile": 0.99,
            "threshold": anomaly_threshold,
            "quality": anomaly_quality,
            "official_event_detection": event_detection,
            "events_alerted": int(sum(x["pre_failure_alert"] for x in event_detection)),
            "event_count": len(event_detection),
        },
        "artifacts": {
            "failure_horizon_model": str(model_path),
            "anomaly_model": str(anomaly_path),
            "holdout_scores": str(compact),
        },
        "artifact_integrity": {
            "failure_horizon_model": _integrity_descriptor(
                model_path, f"models/{model_path.name}"
            ),
            "anomaly_model": _integrity_descriptor(anomaly_path, f"models/{anomaly_path.name}"),
            "holdout_scores": _integrity_descriptor(compact, f"models/{compact.name}"),
        },
        "claim_boundary": (
            "Model evaluation uses real MetroPT-3 operational telemetry and published failure windows. "
            "The failure-horizon label is derived from those event times; it is not a certified row-level failure label or field SLA."
        ),
    }


def _serialize_frame_sample(frame: pd.DataFrame, max_rows: int = 720) -> list[dict[str, Any]]:
    if len(frame) > max_rows:
        step = max(1, math.ceil(len(frame) / max_rows))
        frame = frame.iloc[::step].head(max_rows)
    out = frame.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"]).astype(str)
    return json.loads(out.to_json(orient="records"))


def build_runtime_evidence(
    validation: RawValidation,
    feature_frame: pd.DataFrame,
    model_evidence: dict[str, Any],
    runtime_path: str | Path,
) -> dict[str, Any]:
    frame = feature_frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    quality_gate = model_evidence.get("quality_gate", {})
    model_ready = bool(quality_gate.get("passed") is True and quality_gate.get("promotion_allowed") is True)
    # Keep a derived, low-volume sensor timeline around each official failure for UI evidence.
    windows = []
    for event in METROPT_FAILURE_EVENTS:
        start = event.start_ts - pd.Timedelta(hours=8)
        end = event.end_ts + pd.Timedelta(hours=4)
        segment = frame[(frame["timestamp"] >= start) & (frame["timestamp"] <= end)]
        cols = [
            "timestamp", "TP2_mean", "TP3_mean", "Reservoirs_mean", "Oil_temperature_mean",
            "Motor_current_mean", "pressure_drop_mean", "failure_active", "failure_within_24h",
        ]
        windows.append({"event": asdict(event), "observations": _serialize_frame_sample(segment[cols], 300)})
    feature_store_path = Path(runtime_path).parent / "metropt_feature_store.csv.gz"
    runtime = {
        "status": "READY_FOR_REAL_OPERATIONS" if model_ready else "REAL_DATA_PREPARED_MODEL_GATE_BLOCKED",
        "source_status": "READY",
        "model_status": "PROMOTION_READY" if model_ready else "PROMOTION_BLOCKED",
        "dataset": {
            "id": METROPT_DATASET_ID,
            "name": "MetroPT-3 Air Production Unit",
            "source": "UCI Machine Learning Repository",
            "doi": METROPT_DOI,
            "license": METROPT_LICENSE,
            "evidence_class": "REAL_OPERATIONAL_TELEMETRY",
            "raw_validation": validation.to_dict(),
            "failure_events": [asdict(x) for x in METROPT_FAILURE_EVENTS],
            "raw_data_packaged": False,
        },
        "model": model_evidence,
        "event_windows": windows,
        "feature_store": {
            "rows": len(frame),
            "start": str(frame["timestamp"].min()),
            "end": str(frame["timestamp"].max()),
            "bucket_minutes": round((frame["timestamp"].iloc[1] - frame["timestamp"].iloc[0]).total_seconds() / 60) if len(frame) > 1 else None,
        },
        "integrity": {
            "schema_version": METROPT_RUNTIME_INTEGRITY_SCHEMA_VERSION,
            "feature_store": _integrity_descriptor(
                feature_store_path, f"derived/{feature_store_path.name}"
            ),
            "model_artifacts": model_evidence.get("artifact_integrity", {}),
        },
        "claim_boundary": "Observed telemetry and company-reported failure windows are separated from derived labels, predictions, optimized decisions and simulated consequences.",
    }
    path = Path(runtime_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized_runtime = json.dumps(runtime, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(serialized_runtime)
    digest_path = path.parent / METROPT_RUNTIME_DIGEST_FILENAME
    digest_path.write_text(f"{sha256(serialized_runtime).hexdigest()}\n", encoding="utf-8")
    return runtime


def prepare_metropt3(
    root: str | Path,
    *,
    csv_path: str | Path | None = None,
    download_if_missing: bool = False,
    strict_full_dataset: bool = True,
    bucket_minutes: int = 10,
    seed: int = 731,
) -> dict[str, Any]:
    root = Path(root)
    raw_root = root / "raw"
    derived_root = root / "derived"
    model_root = root / "models"
    raw_root.mkdir(parents=True, exist_ok=True)
    derived_root.mkdir(parents=True, exist_ok=True)
    model_root.mkdir(parents=True, exist_ok=True)
    source = Path(csv_path) if csv_path else raw_root / METROPT_ARCHIVE_MEMBER
    acquisition: dict[str, Any] = {"status": "USER_SUPPLIED_PATH", "csv_path": str(source)}
    if not source.exists():
        if not download_if_missing:
            raise FileNotFoundError(
                f"MetroPT-3 raw CSV not found at {source}. Use --download or supply --csv-path."
            )
        acquisition = download_metropt3(raw_root)
        source = Path(acquisition["csv_path"])
    validation = validate_metropt3_csv(source, strict_full_dataset=strict_full_dataset)
    feature_path = derived_root / "metropt_feature_store.csv.gz"
    features = build_metropt_feature_store(source, feature_path, bucket_minutes=bucket_minutes)
    model_evidence = train_metropt_models(features, model_root, seed=seed, horizon_hours=24)
    runtime_path = derived_root / "metropt_runtime.json"
    runtime = build_runtime_evidence(validation, features, model_evidence, runtime_path)
    model_ready = runtime["model_status"] == "PROMOTION_READY"
    manifest = {
        "dataset_id": METROPT_DATASET_ID,
        "acquisition": acquisition,
        "raw_validation": validation.to_dict(),
        "feature_store_path": str(feature_path),
        "runtime_path": str(runtime_path),
        "model": model_evidence,
        "status": runtime["status"],
        "source_status": runtime["source_status"],
        "model_status": runtime["model_status"],
        "next_action": (
            None
            if model_ready
            else "Improve the real-data validation and future-holdout model gate with additional independent labeled failure history; do not promote this model."
        ),
        "evidence_class": "REAL_OPERATIONAL_DATA_END_TO_END_VALIDATION",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def load_metropt_runtime(root: str | Path) -> dict[str, Any] | None:
    path = Path(root) / "derived" / "metropt_runtime.json"
    if not path.exists():
        return None
    runtime = json.loads(path.read_text(encoding="utf-8"))
    runtime["integrity_check"] = verify_metropt_runtime_integrity(root, runtime)
    return runtime


def load_metropt_feature_store(root: str | Path) -> pd.DataFrame:
    path = Path(root) / "derived" / "metropt_feature_store.csv.gz"
    if not path.exists():
        raise FileNotFoundError(path)
    # The derived event-id column intentionally mixes blank values and labels.
    # Read in one pass so pandas does not emit a misleading mixed-type warning
    # during operational diagnostics and replay.
    frame = pd.read_csv(path, low_memory=False)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="raise")
    return frame


def load_metropt_models(root: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = load_metropt_runtime(root)
    if runtime is not None and "integrity" in runtime:
        integrity = runtime.get("integrity_check", {})
        if integrity.get("passed") is not True:
            raise RuntimeError(
                "MetroPT runtime integrity verification failed; model artifacts are not trusted"
            )
    model_root = Path(root) / "models"
    failure = joblib.load(model_root / "metropt_failure_horizon.joblib")
    anomaly = joblib.load(model_root / "metropt_anomaly.joblib")
    return failure, anomaly


def score_metropt_frame(root: str | Path, frame: pd.DataFrame) -> pd.DataFrame:
    failure, anomaly = load_metropt_models(root)
    out = frame.copy()
    fail_prob = failure["model"].predict_proba(out[failure["features"]])[:, 1]
    anomaly_score = -anomaly["model"].decision_function(anomaly["scaler"].transform(out[anomaly["features"]]))
    out["failure_probability"] = fail_prob
    out["anomaly_score"] = anomaly_score
    out["failure_alert"] = fail_prob >= float(failure["threshold"])
    out["anomaly_alert"] = anomaly_score >= float(anomaly["threshold"])
    return out
