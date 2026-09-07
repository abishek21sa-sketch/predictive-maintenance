from __future__ import annotations

from pathlib import Path

import pandas as pd

CMAPSS_COLUMNS = (
    ["unit_id", "cycle", "setting_1", "setting_2", "setting_3"]
    + [f"sensor_{i:02d}" for i in range(1, 22)]
)
SENSOR_COLUMNS = [f"sensor_{i:02d}" for i in range(1, 22)]
SETTING_COLUMNS = ["setting_1", "setting_2", "setting_3"]


def load_trajectory(path: str | Path) -> pd.DataFrame:
    """Load a C-MAPSS trajectory file and validate the canonical 26-column schema."""
    df = pd.read_csv(path, sep=r"\s+", header=None, engine="python")
    if df.shape[1] != len(CMAPSS_COLUMNS):
        raise ValueError(f"Expected 26 C-MAPSS columns, got {df.shape[1]}")
    df.columns = CMAPSS_COLUMNS
    return validate(df)


def load_rul(path: str | Path) -> pd.Series:
    values = pd.read_csv(path, sep=r"\s+", header=None, engine="python").iloc[:, 0]
    values.name = "rul"
    return values.astype(float)


def validate(df: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(CMAPSS_COLUMNS) - set(df.columns))
    if missing:
        raise ValueError(f"Missing C-MAPSS fields: {missing}")
    if df[CMAPSS_COLUMNS].isna().any().any():
        raise ValueError("C-MAPSS data contains nulls in required fields")
    if (df["cycle"] <= 0).any() or (df["unit_id"] <= 0).any():
        raise ValueError("unit_id and cycle must be positive")
    ordered = df.sort_values(["unit_id", "cycle"]).reset_index(drop=True)
    bad = ordered.groupby("unit_id")["cycle"].diff().dropna().le(0).any()
    if bad:
        raise ValueError("Cycles must increase strictly within each unit")
    return ordered


def add_training_rul(df: pd.DataFrame, cap: int | None = 125) -> pd.DataFrame:
    out = validate(df).copy()
    max_cycle = out.groupby("unit_id")["cycle"].transform("max")
    raw_rul = max_cycle - out["cycle"]
    out["rul"] = raw_rul.clip(upper=cap) if cap is not None else raw_rul
    return out


def add_test_rul(df: pd.DataFrame, terminal_rul: pd.Series) -> pd.DataFrame:
    out = validate(df).copy()
    units = sorted(out["unit_id"].unique())
    if len(units) != len(terminal_rul):
        raise ValueError("RUL vector length does not match test unit count")
    terminal = dict(zip(units, terminal_rul.astype(float).tolist()))
    last_cycles = out.groupby("unit_id")["cycle"].max().to_dict()
    out["rul"] = out.apply(
        lambda row: terminal[int(row.unit_id)] + last_cycles[int(row.unit_id)] - row.cycle,
        axis=1,
    )
    return out
