from __future__ import annotations

import pandas as pd

from pdm_intelligence.data.cmapss import SENSOR_COLUMNS


def engineer_features(df: pd.DataFrame, rolling_window: int = 5) -> pd.DataFrame:
    """Create leakage-safe per-cycle features using only current/past observations."""
    out = df.sort_values(["unit_id", "cycle"]).copy()
    g = out.groupby("unit_id", group_keys=False)
    for sensor in SENSOR_COLUMNS:
        out[f"{sensor}_delta"] = g[sensor].diff().fillna(0.0)
        out[f"{sensor}_roll_mean"] = g[sensor].transform(
            lambda s: s.rolling(rolling_window, min_periods=1).mean()
        )
        out[f"{sensor}_roll_std"] = g[sensor].transform(
            lambda s: s.rolling(rolling_window, min_periods=2).std().fillna(0.0)
        )
    return out


def model_feature_columns(df: pd.DataFrame) -> list[str]:
    excluded = {"unit_id", "rul"}
    return [c for c in df.columns if c not in excluded and pd.api.types.is_numeric_dtype(df[c])]
