from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MaintenanceKPIs:
    planned_maintenance_percentage: float
    schedule_compliance: float
    emergency_work_percentage: float
    mean_maintenance_duration: float
    maintenance_cost_per_operating_cycle: float

    def to_dict(self):
        return asdict(self)


def compute_maintenance_kpis(events: list[dict], operating_cycles: float) -> MaintenanceKPIs:
    if not events or operating_cycles <= 0:
        raise ValueError("Maintenance events and positive operating cycles are required")
    n = len(events)
    planned = sum(bool(e.get("planned", False)) for e in events)
    emergency = sum(bool(e.get("emergency", False)) for e in events)
    scheduled = [e for e in events if e.get("planned", False)]
    compliant = sum(bool(e.get("on_schedule", False)) for e in scheduled)
    duration = sum(float(e.get("duration", 0.0)) for e in events) / n
    cost = sum(float(e.get("cost", 0.0)) for e in events) / operating_cycles
    return MaintenanceKPIs(
        planned_maintenance_percentage=100.0 * planned / n,
        schedule_compliance=100.0 * compliant / len(scheduled) if scheduled else 0.0,
        emergency_work_percentage=100.0 * emergency / n,
        mean_maintenance_duration=duration,
        maintenance_cost_per_operating_cycle=cost,
    )
