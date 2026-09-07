from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FailureMode:
    code: str
    component: str
    failure_mode: str
    effect: str
    severity: int
    occurrence: int
    detection: int
    safety_critical: bool = False
    condition_detectable: bool = True

    @property
    def rpn(self) -> int:
        return int(self.severity * self.occurrence * self.detection)

    def to_dict(self):
        return asdict(self) | {"rpn": self.rpn, "rcm_strategy": recommend_rcm_strategy(self)}


def recommend_rcm_strategy(mode: FailureMode) -> str:
    """Transparent RCM screening rule for task selection."""
    if mode.safety_critical and mode.condition_detectable:
        return "condition_based_maintenance"
    if mode.safety_critical:
        return "scheduled_restoration_or_redesign"
    if mode.condition_detectable and mode.rpn >= 120:
        return "condition_based_maintenance"
    if mode.rpn >= 80:
        return "scheduled_inspection"
    return "run_to_failure_with_monitoring"


def prioritize_failure_modes(modes: list[FailureMode]) -> list[dict]:
    return [m.to_dict() for m in sorted(modes, key=lambda x: (-x.rpn, -x.severity, x.code))]


def turbofan_fd001_fmea() -> list[FailureMode]:
    """Illustrative engineering FMEA aligned to the FD001 HPC-degradation use case.

    Ratings are repository assumptions for decision workflow demonstration, not NASA labels.
    """
    return [
        FailureMode("FM-HPC-01", "High-pressure compressor", "Progressive efficiency degradation", "Loss of performance and eventual engine failure", 9, 6, 4, True, True),
        FailureMode("FM-SEN-01", "Sensor system", "Bias/drift", "Incorrect health-state estimation", 7, 4, 5, False, True),
        FailureMode("FM-MNT-01", "Maintenance process", "Deferred intervention", "Higher probability of unscheduled removal", 8, 3, 6, False, True),
    ]
