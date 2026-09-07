from __future__ import annotations

import numpy as np
import pandas as pd


def _psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    ref = ref[np.isfinite(ref)]
    cur = cur[np.isfinite(cur)]
    if len(ref) < 5 or len(cur) < 5:
        return 0.0
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    ref_probability = np.histogram(ref, bins=edges)[0] / len(ref)
    current_probability = np.histogram(cur, bins=edges)[0] / len(cur)
    ref_probability = np.clip(ref_probability, 1e-6, None)
    current_probability = np.clip(current_probability, 1e-6, None)
    return float(np.sum((current_probability - ref_probability) * np.log(current_probability / ref_probability)))

def feature_drift_report(reference: pd.DataFrame, current: pd.DataFrame, columns: list[str] | None=None) -> dict:
    cols = columns or [
        c for c in reference.columns
        if c in current.columns and pd.api.types.is_numeric_dtype(reference[c])
    ]
    details = []
    insufficient = []
    for column in cols:
        ref_count = int(reference[column].replace([np.inf, -np.inf], np.nan).dropna().size)
        current_count = int(current[column].replace([np.inf, -np.inf], np.nan).dropna().size)
        if ref_count < 5 or current_count < 5:
            insufficient.append(column)
        psi = _psi(reference[column].to_numpy(), current[column].to_numpy())
        level = "high" if psi >= 0.25 else "moderate" if psi >= 0.10 else "low"
        details.append({"feature": column, "psi": round(psi, 6), "level": level,
                        "reference_count": ref_count, "current_count": current_count})
    worst = max((item["psi"] for item in details), default=0.0)
    status = "insufficient_evidence" if insufficient else (
        "drift_detected" if worst >= 0.25 else "watch" if worst >= 0.10 else "stable"
    )
    return {
        "status": status,
        "max_psi": round(worst, 6),
        "features": details,
        "insufficient_features": insufficient,
        "thresholds": {"watch": 0.10, "drift_detected": 0.25},
        "evidence_class": "REFERENCE_FEATURE_DISTRIBUTION_MONITORING",
    }
