from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

import numpy as np

from pdm_intelligence.optimization.intervention import InterventionChoice
from pdm_intelligence.optimization.maintenance import AssetMaintenanceInput


@dataclass(frozen=True)
class StressConfig:
    rul_sigma_fraction: float = 0.20
    duration_sigma_fraction: float = 0.20
    bay_outage_probability: float = 0.08
    technician_absence_probability: float = 0.10
    part_delay_probability: float = 0.08
    disruption_delay_cycles: int = 2
    corrective_downtime_low: float = 3.0
    corrective_downtime_high: float = 9.0
    downtime_cost_per_cycle: float = 1200.0
    preventive_cost: float = 5000.0
    failure_cost: float = 30000.0
    emergency_penalty: float = 12000.0


@dataclass(frozen=True)
class StressPolicyResult:
    policy: str
    expected_total_cost: float
    p95_total_cost: float
    expected_failures: float
    expected_downtime_cycles: float
    expected_disrupted_interventions: float
    on_time_intervention_rate: float

    def to_dict(self):
        return asdict(self)


def _heuristic_choices(items: list[AssetMaintenanceInput], capacity: int = 2) -> list[InterventionChoice]:
    ranked = sorted(items, key=lambda x: (x.predicted_rul, -x.failure_probability))
    lane_end = [0] * max(1, capacity)
    out = []
    for item in ranked:
        lane = int(np.argmin(lane_end))
        start = lane_end[lane] + 1
        lane_end[lane] = start + item.duration - 1
        out.append(InterventionChoice(item.asset_id, "maintain", start, start + item.duration - 1,
                                      item.duration, 0.0, item.required_skill, item.part_id, item.labor_hours))
    return out


def _simulate_policy(
    policy: str,
    items: list[AssetMaintenanceInput],
    choices_by_asset: dict[int, InterventionChoice] | None,
    draws: dict[str, np.ndarray],
    cfg: StressConfig,
) -> StressPolicyResult:
    n_sim, _ = draws["rul_z"].shape
    costs = np.zeros(n_sim); failures = np.zeros(n_sim); downtime = np.zeros(n_sim)
    disrupted = np.zeros(n_sim); ontime = np.zeros(n_sim); actionable = np.zeros(n_sim)
    for k in range(n_sim):
        for i, item in enumerate(items):
            sigma = max(2.0, cfg.rul_sigma_fraction * max(item.predicted_rul, 1.0))
            realized_rul = max(1.0, item.predicted_rul + draws["rul_z"][k, i] * sigma)
            if policy == "run_to_failure":
                down = cfg.corrective_downtime_low + draws["downtime_u"][k, i] * (cfg.corrective_downtime_high - cfg.corrective_downtime_low)
                costs[k] += cfg.failure_cost + cfg.emergency_penalty + down * cfg.downtime_cost_per_cycle
                failures[k] += 1; downtime[k] += down
                continue
            choice = choices_by_asset[item.asset_id]
            if choice.action == "defer":
                if realized_rul <= 5:
                    down = cfg.corrective_downtime_low + draws["downtime_u"][k, i] * (cfg.corrective_downtime_high - cfg.corrective_downtime_low)
                    costs[k] += cfg.failure_cost + cfg.emergency_penalty + down * cfg.downtime_cost_per_cycle
                    failures[k] += 1; downtime[k] += down
                else:
                    costs[k] += 0.15 * cfg.preventive_cost
                continue
            actionable[k] += 1
            start = float(choice.start_cycle or 1)
            delay = 0
            if draws["bay_u"][k, i] < cfg.bay_outage_probability: delay += cfg.disruption_delay_cycles
            if draws["tech_u"][k, i] < cfg.technician_absence_probability: delay += cfg.disruption_delay_cycles
            if choice.action == "maintain" and draws["part_u"][k, i] < cfg.part_delay_probability: delay += cfg.disruption_delay_cycles
            disrupted[k] += 1 if delay else 0
            realized_start = start + delay
            duration_noise = max(0.5, 1.0 + cfg.duration_sigma_fraction * draws["duration_z"][k, i])
            realized_duration = max(0.5, choice.duration * duration_noise)
            if realized_rul < realized_start:
                down = cfg.corrective_downtime_low + draws["downtime_u"][k, i] * (cfg.corrective_downtime_high - cfg.corrective_downtime_low)
                costs[k] += cfg.failure_cost + cfg.emergency_penalty + down * cfg.downtime_cost_per_cycle
                failures[k] += 1; downtime[k] += down
            elif choice.action == "inspect":
                costs[k] += 2200.0 + 0.5 * realized_duration * cfg.downtime_cost_per_cycle
                downtime[k] += 0.5 * realized_duration
                ontime[k] += 1 if delay == 0 else 0
            else:
                costs[k] += cfg.preventive_cost + realized_duration * cfg.downtime_cost_per_cycle
                downtime[k] += realized_duration
                ontime[k] += 1 if delay == 0 else 0
    rate = float(np.sum(ontime) / max(1.0, np.sum(actionable)))
    return StressPolicyResult(policy, float(np.mean(costs)), float(np.quantile(costs, 0.95)),
                              float(np.mean(failures)), float(np.mean(downtime)),
                              float(np.mean(disrupted)), rate)


def compare_plan_under_stress(
    items: list[AssetMaintenanceInput],
    optimized_choices: Iterable[InterventionChoice],
    *,
    n_simulations: int = 1000,
    seed: int = 42,
    config: StressConfig | None = None,
    heuristic_capacity: int = 2,
) -> dict:
    if n_simulations <= 0:
        raise ValueError("n_simulations must be positive")
    cfg = config or StressConfig()
    rng = np.random.default_rng(seed)
    shape = (n_simulations, len(items))
    draws = {
        "rul_z": rng.normal(size=shape),
        "duration_z": rng.normal(size=shape),
        "bay_u": rng.random(shape),
        "tech_u": rng.random(shape),
        "part_u": rng.random(shape),
        "downtime_u": rng.random(shape),
    }
    optimized = {x.asset_id: x for x in optimized_choices}
    if set(optimized) != {x.asset_id for x in items}:
        raise ValueError("optimized_choices must contain exactly one choice per asset")
    heuristic = {x.asset_id: x for x in _heuristic_choices(items, heuristic_capacity)}
    results = [
        _simulate_policy("optimized_intervention", items, optimized, draws, cfg),
        _simulate_policy("earliest_rul_heuristic", items, heuristic, draws, cfg),
        _simulate_policy("run_to_failure", items, None, draws, cfg),
    ]
    return {
        "evidence_class": "SEEDED_MONTE_CARLO_STRESS_SIMULATION",
        "seed": seed,
        "n_simulations": n_simulations,
        "common_random_numbers": True,
        "config": asdict(cfg),
        "results": [x.to_dict() for x in results],
        "claim_boundary": "Modeled stress-test consequences only; not realized field savings or causal performance.",
    }
