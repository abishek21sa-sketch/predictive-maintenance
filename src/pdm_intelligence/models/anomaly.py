from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from pdm_intelligence.data.cmapss import SENSOR_COLUMNS


class AnomalyDetector:
    def __init__(self, seed: int = 42):
        self.scaler = StandardScaler()
        self.model = IsolationForest(n_estimators=150, contamination=0.08, random_state=seed)
        self._fit = False
        self.score_lo = 0.0
        self.score_hi = 1.0

    def fit(self, df: pd.DataFrame):
        first_cycles = df[df["cycle"] <= df.groupby("unit_id")["cycle"].transform("max") * 0.25]
        X = self.scaler.fit_transform(first_cycles[SENSOR_COLUMNS])
        self.model.fit(X)
        raw = -self.model.score_samples(X)
        self.score_lo, self.score_hi = map(float, np.percentile(raw, [5, 95]))
        if self.score_hi <= self.score_lo:
            self.score_hi = self.score_lo + 1e-9
        self._fit = True
        return self

    def score(self, df: pd.DataFrame) -> pd.Series:
        if not self._fit:
            raise RuntimeError("AnomalyDetector is not fitted")
        X = self.scaler.transform(df[SENSOR_COLUMNS])
        raw = -self.model.score_samples(X)
        scaled = np.clip((raw - self.score_lo) / (self.score_hi - self.score_lo), 0, 1)
        return pd.Series(scaled, index=df.index, name="anomaly_score")
