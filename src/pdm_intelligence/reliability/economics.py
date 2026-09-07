from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MaintenanceEconomics:
    preventive_cost: float
    expected_failure_cost: float
    expected_downtime_cost: float
    expected_total_cost: float
    expected_avoided_cost_vs_run_to_failure: float

    def to_dict(self):
        return asdict(self)


def expected_maintenance_economics(
    failure_probability: float,
    preventive_cost: float = 5000.0,
    corrective_cost: float = 30000.0,
    corrective_downtime_cycles: float = 6.0,
    preventive_downtime_cycles: float = 1.0,
    downtime_cost_per_cycle: float = 1200.0,
) -> MaintenanceEconomics:
    if not 0 <= failure_probability <= 1:
        raise ValueError("failure_probability must be in [0, 1]")
    values = [preventive_cost, corrective_cost, corrective_downtime_cycles,
              preventive_downtime_cycles, downtime_cost_per_cycle]
    if any(v < 0 for v in values):
        raise ValueError("costs and downtime values cannot be negative")

    expected_failure = failure_probability * corrective_cost
    expected_downtime = (
        failure_probability * corrective_downtime_cycles
        + (1.0 - failure_probability) * preventive_downtime_cycles
    ) * downtime_cost_per_cycle
    preventive_total = preventive_cost + expected_failure + expected_downtime
    run_to_failure = corrective_cost + corrective_downtime_cycles * downtime_cost_per_cycle
    return MaintenanceEconomics(
        preventive_cost=float(preventive_cost),
        expected_failure_cost=float(expected_failure),
        expected_downtime_cost=float(expected_downtime),
        expected_total_cost=float(preventive_total),
        expected_avoided_cost_vs_run_to_failure=float(run_to_failure - preventive_total),
    )
