from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from scipy.stats import weibull_min


@dataclass(frozen=True)
class ReliabilitySummary:
    mtbf_cycles: float
    mttr_cycles: float
    availability: float
    weibull_shape: float
    weibull_scale: float
    hazard_phase: str

    def to_dict(self):
        return asdict(self)


def fit_reliability(failure_cycles, repair_durations=None) -> ReliabilitySummary:
    failures = np.asarray(failure_cycles, dtype=float)
    if failures.size < 3 or np.any(failures <= 0):
        raise ValueError("At least three positive failure lifetimes are required")
    repairs = np.asarray(repair_durations if repair_durations is not None else np.full_like(failures, 8.0), dtype=float)
    if np.any(repairs < 0):
        raise ValueError("Repair durations cannot be negative")
    shape, _, scale = weibull_min.fit(failures, floc=0)
    mtbf = float(failures.mean())
    mttr = float(repairs.mean())
    availability = mtbf / (mtbf + mttr) if mtbf + mttr else 0.0
    if shape < 0.95:
        phase = "infant_mortality"
    elif shape <= 1.05:
        phase = "random_failure"
    else:
        phase = "wear_out"
    return ReliabilitySummary(mtbf, mttr, availability, float(shape), float(scale), phase)


def weibull_hazard(cycles, shape: float, scale: float):
    x = np.asarray(cycles, dtype=float)
    return (shape / scale) * np.power(np.maximum(x, 1e-9) / scale, shape - 1)


def bathtub_hazard(cycles, early_rate: float = 0.08, random_rate: float = 0.01, wear_rate: float = 0.06, transition: float = 100.0):
    """Three-component bathtub-curve hazard for lifecycle reasoning/visualization."""
    x = np.asarray(cycles, dtype=float)
    if np.any(x < 0):
        raise ValueError("Lifecycle age cannot be negative")
    early = early_rate * np.exp(-x / max(transition * 0.25, 1e-9))
    random = np.full_like(x, random_rate, dtype=float)
    wear = wear_rate * np.power(np.maximum(x / max(transition, 1e-9), 0.0), 3.0)
    return {"early": early, "random": random, "wear": wear, "total": early + random + wear}
