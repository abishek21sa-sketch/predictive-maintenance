from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

MIN_EXTERNAL_RUL_ASSETS = 6

CMAPSS_REQUIRED = [
    "cycle", "setting_1", "setting_2", "setting_3",
    *[f"sensor_{i:02d}" for i in range(1, 22)],
]


@dataclass(frozen=True)
class Capability:
    status: str
    evidence: list[str]
    missing: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cap(status: str, evidence: list[str] | None = None, missing: list[str] | None = None) -> Capability:
    return Capability(status=status, evidence=evidence or [], missing=missing or [])


def assess_readiness(
    assets: pd.DataFrame | None = None,
    telemetry: pd.DataFrame | None = None,
    maintenance: pd.DataFrame | None = None,
) -> dict[str, Any]:
    caps: dict[str, Capability] = {}
    assets = assets if assets is not None else pd.DataFrame()
    telemetry = telemetry if telemetry is not None else pd.DataFrame()
    maintenance = maintenance if maintenance is not None else pd.DataFrame()

    if not telemetry.empty:
        caps["asset_registry"] = _cap(
            "READY",
            [f"{telemetry['asset_id'].nunique()} assets represented in telemetry"],
        )
    elif not assets.empty:
        caps["asset_registry"] = _cap("READY", [f"{len(assets)} asset-master records"])
    else:
        caps["asset_registry"] = _cap("NOT_READY", missing=["asset master or telemetry asset_id"])

    signal_cols = [
        c for c in telemetry.columns
        if c not in {"asset_id", "cycle", "timestamp", "rul"}
        and pd.api.types.is_numeric_dtype(telemetry[c])
    ]
    if signal_cols:
        caps["condition_monitoring"] = _cap(
            "READY", [f"{len(signal_cols)} numeric condition signals", f"{len(telemetry)} observations"]
        )
    else:
        caps["condition_monitoring"] = _cap("NOT_READY", missing=["numeric telemetry signals"])

    missing_fd001 = [c for c in CMAPSS_REQUIRED if c not in telemetry.columns]
    if telemetry.empty:
        caps["fd001_rul_inference"] = _cap("NOT_READY", missing=["telemetry"])
    elif not missing_fd001:
        caps["fd001_rul_inference"] = _cap(
            "READY",
            ["schema matches the bundled FD001 model input contract"],
        )
    else:
        caps["fd001_rul_inference"] = _cap(
            "NOT_READY",
            ["Bundled NASA model is not silently applied to incompatible assets"],
            missing=missing_fd001,
        )

    if "rul" in telemetry and telemetry["rul"].notna().all() and telemetry["asset_id"].nunique() >= MIN_EXTERNAL_RUL_ASSETS:
        caps["external_rul_training"] = _cap(
            "READY",
            [
                "explicit RUL target supplied",
                f"{telemetry['asset_id'].nunique()} independent asset trajectories",
                "at least two trajectories reserved for validation and four retained for training",
            ],
        )
    else:
        missing = []
        if "rul" not in telemetry:
            missing.append("rul target")
        if not telemetry.empty and telemetry["asset_id"].nunique() < MIN_EXTERNAL_RUL_ASSETS:
            missing.append(f"at least {MIN_EXTERNAL_RUL_ASSETS} independent asset trajectories")
        caps["external_rul_training"] = _cap(
            "NOT_READY",
            ["External model training requires user-supported ground truth"],
            missing=missing or ["telemetry with RUL labels"],
        )

    if not maintenance.empty:
        has_downtime = "downtime_hours" in maintenance
        has_times = {"start_time", "end_time"}.issubset(maintenance.columns)
        if has_downtime or has_times:
            caps["maintenance_kpis"] = _cap("READY", [f"{len(maintenance)} maintenance events"])
        else:
            caps["maintenance_kpis"] = _cap(
                "PARTIAL", [f"{len(maintenance)} events"], ["downtime_hours or start/end timestamps"]
            )
        if "failure" in maintenance or maintenance["event_type"].str.contains("failure|corrective", regex=True).any():
            caps["failure_reliability"] = _cap("READY", ["failure/corrective events are identifiable"])
        else:
            caps["failure_reliability"] = _cap("PARTIAL", ["maintenance history present"], ["failure labels"])
    else:
        caps["maintenance_kpis"] = _cap("NOT_READY", missing=["maintenance history"])
        caps["failure_reliability"] = _cap("NOT_READY", missing=["failure-labelled maintenance history"])

    planner_evidence = []
    planner_missing = []
    if not assets.empty and "criticality" in assets:
        planner_evidence.append("asset criticality available")
    else:
        planner_missing.append("asset criticality (defaults/assumptions required)")
    if not maintenance.empty and "labor_hours" in maintenance:
        planner_evidence.append("historical labor requirements available")
    else:
        planner_missing.append("labor requirements (planner assumptions required)")
    caps["maintenance_optimization"] = _cap(
        "READY_WITH_ASSUMPTIONS" if signal_cols else "NOT_READY",
        planner_evidence + (["condition state available"] if signal_cols else []),
        planner_missing + ([] if signal_cols else ["condition/prognostic inputs"]),
    )

    replay_ready = not telemetry.empty and ("cycle" in telemetry or "timestamp" in telemetry)
    caps["historical_replay"] = _cap(
        "READY" if replay_ready else "NOT_READY",
        ["ordered trajectory axis available"] if replay_ready else [],
        [] if replay_ready else ["cycle or timestamp"],
    )

    statuses = [c.status for c in caps.values()]
    ready_count = sum(s in {"READY", "READY_WITH_ASSUMPTIONS"} for s in statuses)
    return {
        "overall": "READY_FOR_ANALYSIS" if ready_count >= 3 else "LIMITED",
        "capabilities": {k: v.to_dict() for k, v in caps.items()},
        "quality": _quality_summary(assets, telemetry, maintenance),
    }


def _quality_summary(assets: pd.DataFrame, telemetry: pd.DataFrame, maintenance: pd.DataFrame) -> dict[str, Any]:
    frames = {"assets": assets, "telemetry": telemetry, "maintenance": maintenance}
    result: dict[str, Any] = {}
    for name, frame in frames.items():
        if frame.empty:
            result[name] = {"rows": 0, "missing_fraction": None}
            continue
        denom = max(1, frame.shape[0] * frame.shape[1])
        result[name] = {
            "rows": len(frame),
            "columns": int(frame.shape[1]),
            "missing_fraction": float(frame.isna().sum().sum() / denom),
            "duplicate_rows": int(frame.duplicated().sum()),
        }
    if not telemetry.empty:
        numeric = telemetry.select_dtypes(include=[np.number])
        result["telemetry"]["constant_numeric_signals"] = [
            c for c in numeric.columns if c not in {"cycle", "rul"} and numeric[c].nunique(dropna=True) <= 1
        ]
    return result
