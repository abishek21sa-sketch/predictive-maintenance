from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

ASSET_REQUIRED = ("asset_id",)
TELEMETRY_REQUIRED_ANY = (("asset_id", "cycle"), ("asset_id", "timestamp"))
MAINTENANCE_REQUIRED = ("asset_id", "event_type")

ASSET_ALIASES = {
    "asset_id": ("asset_id", "unit_id", "machine_id", "equipment_id", "engine_id", "id"),
    "asset_type": ("asset_type", "equipment_type", "machine_type", "type"),
    "criticality": ("criticality", "asset_criticality", "priority_class"),
    "status": ("status", "asset_status"),
    "commissioned_at": ("commissioned_at", "install_date", "commission_date", "installed_at"),
}

TELEMETRY_ALIASES = {
    "asset_id": ASSET_ALIASES["asset_id"],
    "cycle": ("cycle", "operating_cycle", "time_step", "step", "sequence"),
    "timestamp": ("timestamp", "datetime", "date_time", "event_time", "time"),
    "rul": ("rul", "remaining_useful_life", "remaining_life", "target_rul"),
}

MAINTENANCE_ALIASES = {
    "asset_id": ASSET_ALIASES["asset_id"],
    "event_type": ("event_type", "maintenance_type", "work_type", "event", "type"),
    "start_time": ("start_time", "start", "maintenance_start", "opened_at"),
    "end_time": ("end_time", "end", "maintenance_end", "closed_at"),
    "downtime_hours": ("downtime_hours", "downtime", "duration_hours"),
    "labor_hours": ("labor_hours", "labor", "work_hours"),
    "part": ("part", "part_id", "part_number", "component"),
    "cost": ("cost", "maintenance_cost", "total_cost"),
    "failure": ("failure", "is_failure", "corrective_failure"),
}


def normalize_name(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")


def infer_mapping(columns: Iterable[str], entity_type: str) -> dict[str, str]:
    normalized = {normalize_name(c): c for c in columns}
    aliases = {
        "assets": ASSET_ALIASES,
        "telemetry": TELEMETRY_ALIASES,
        "maintenance": MAINTENANCE_ALIASES,
    }.get(entity_type)
    if aliases is None:
        raise ValueError(f"Unsupported entity_type: {entity_type}")
    mapping: dict[str, str] = {}
    for canonical, candidates in aliases.items():
        for candidate in candidates:
            if normalize_name(candidate) in normalized:
                mapping[canonical] = normalized[normalize_name(candidate)]
                break
    return mapping


def apply_mapping(df: pd.DataFrame, entity_type: str, mapping: dict[str, str] | None = None) -> pd.DataFrame:
    inferred = infer_mapping(df.columns, entity_type)
    merged = dict(inferred)
    if mapping:
        # API mapping is canonical -> source-column.
        merged.update({k: v for k, v in mapping.items() if v})
    rename = {source: canonical for canonical, source in merged.items() if source in df.columns}
    out = df.rename(columns=rename).copy()
    out.columns = [normalize_name(c) for c in out.columns]
    return out


def _coerce_asset_id(df: pd.DataFrame) -> pd.DataFrame:
    if "asset_id" not in df:
        raise ValueError("A canonical asset_id field is required")
    out = df.copy()
    if out["asset_id"].isna().any():
        raise ValueError("asset_id contains null values")
    out["asset_id"] = out["asset_id"].astype(str).str.strip()
    if (out["asset_id"] == "").any():
        raise ValueError("asset_id contains blank values")
    return out


def validate_assets(df: pd.DataFrame) -> pd.DataFrame:
    out = _coerce_asset_id(df)
    if out["asset_id"].duplicated().any():
        dup = out.loc[out["asset_id"].duplicated(), "asset_id"].iloc[0]
        raise ValueError(f"Duplicate asset_id in asset master: {dup}")
    if "criticality" in out:
        out["criticality"] = out["criticality"].astype(str).str.lower()
    return out.reset_index(drop=True)


def validate_telemetry(df: pd.DataFrame) -> pd.DataFrame:
    out = _coerce_asset_id(df)
    if "cycle" not in out and "timestamp" not in out:
        raise ValueError("Telemetry requires either cycle or timestamp")
    if "cycle" in out:
        out["cycle"] = pd.to_numeric(out["cycle"], errors="coerce")
        if out["cycle"].isna().any() or (out["cycle"] < 0).any():
            raise ValueError("cycle must be numeric and non-negative")
    if "timestamp" in out:
        out["timestamp"] = pd.to_datetime(out["timestamp"], errors="coerce", utc=True)
        if out["timestamp"].isna().any():
            raise ValueError("timestamp contains unparseable values")
    key = ["asset_id", "cycle"] if "cycle" in out else ["asset_id", "timestamp"]
    if out.duplicated(key).any():
        raise ValueError(f"Duplicate telemetry observations for key {key}")
    order_col = "cycle" if "cycle" in out else "timestamp"
    out = out.sort_values(["asset_id", order_col]).reset_index(drop=True)
    diff = out.groupby("asset_id")[order_col].diff().dropna()
    bad = diff.le(pd.Timedelta(0)).any() if order_col == "timestamp" else diff.le(0).any()
    if bad:
        raise ValueError(f"{order_col} must increase strictly within each asset")
    numeric_signal_cols = [
        c for c in out.columns
        if c not in {"asset_id", "cycle", "timestamp", "rul"}
        and pd.api.types.is_numeric_dtype(out[c])
    ]
    if not numeric_signal_cols:
        # Try to coerce candidate signal fields before declaring the dataset empty.
        for c in [x for x in out.columns if x not in {"asset_id", "cycle", "timestamp", "rul"}]:
            converted = pd.to_numeric(out[c], errors="coerce")
            if converted.notna().mean() >= 0.95:
                out[c] = converted
        numeric_signal_cols = [
            c for c in out.columns
            if c not in {"asset_id", "cycle", "timestamp", "rul"}
            and pd.api.types.is_numeric_dtype(out[c])
        ]
    if not numeric_signal_cols:
        raise ValueError("Telemetry contains no usable numeric sensor/condition signals")
    if "rul" in out:
        out["rul"] = pd.to_numeric(out["rul"], errors="coerce")
        if out["rul"].isna().any() or (out["rul"] < 0).any():
            raise ValueError("rul target must be numeric and non-negative")
    return out


def validate_maintenance(df: pd.DataFrame) -> pd.DataFrame:
    out = _coerce_asset_id(df)
    if "event_type" not in out:
        raise ValueError("Maintenance history requires event_type")
    out["event_type"] = out["event_type"].astype(str).str.strip().str.lower()
    if (out["event_type"] == "").any():
        raise ValueError("event_type contains blank values")
    for c in ("start_time", "end_time"):
        if c in out:
            out[c] = pd.to_datetime(out[c], errors="coerce", utc=True)
            if out[c].isna().any():
                raise ValueError(f"{c} contains unparseable values")
    if {"start_time", "end_time"}.issubset(out.columns) and (out["end_time"] < out["start_time"]).any():
        raise ValueError("Maintenance end_time cannot precede start_time")
    for c in ("downtime_hours", "labor_hours", "cost"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce")
            if out[c].isna().any() or (out[c] < 0).any():
                raise ValueError(f"{c} must be numeric and non-negative")
    if "failure" in out:
        vals = out["failure"].astype(str).str.lower()
        out["failure"] = vals.isin({"1", "true", "yes", "y", "failure", "corrective"})
    return out.reset_index(drop=True)


def canonicalize(df: pd.DataFrame, entity_type: str, mapping: dict[str, str] | None = None) -> pd.DataFrame:
    mapped = apply_mapping(df, entity_type, mapping)
    if entity_type == "assets":
        return validate_assets(mapped)
    if entity_type == "telemetry":
        return validate_telemetry(mapped)
    if entity_type == "maintenance":
        return validate_maintenance(mapped)
    raise ValueError(f"Unsupported entity_type: {entity_type}")


@dataclass(frozen=True)
class SchemaSummary:
    entity_type: str
    rows: int
    columns: list[str]
    null_counts: dict[str, int]
    asset_count: int


def summarize(df: pd.DataFrame, entity_type: str) -> SchemaSummary:
    return SchemaSummary(
        entity_type=entity_type,
        rows=len(df),
        columns=list(df.columns),
        null_counts={k: int(v) for k, v in df.isna().sum().to_dict().items()},
        asset_count=int(df["asset_id"].nunique()) if "asset_id" in df else 0,
    )
