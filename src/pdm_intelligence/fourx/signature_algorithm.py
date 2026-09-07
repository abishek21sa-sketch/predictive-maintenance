from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

HEALTH_STATES = ("healthy", "degraded", "critical", "failed")
FAILED_INDEX = 3


@dataclass(frozen=True)
class BeliefMaintAsset:
    asset_id: int
    initial_belief: tuple[float, float, float, float]
    preventive_cost: float
    failure_cost: float
    opportunity_cost_by_cycle: tuple[float, ...]


@dataclass(frozen=True)
class BeliefMaintSchedule:
    asset_id: int
    maintenance_cycle: int
    failure_probability_at_maintenance: float
    cumulative_failure_exposure: float
    opportunity_cost: float
    modeled_cost: float


@dataclass(frozen=True)
class BeliefMaintResult:
    algorithm: str
    status: str
    solver: str
    objective_value: float
    schedule: tuple[BeliefMaintSchedule, ...]
    transition_matrix: tuple[tuple[float, ...], ...]
    horizon: int
    crew_capacity_by_cycle: tuple[int, ...]
    claim_boundary: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["schedule"] = [asdict(x) for x in self.schedule]
        return payload


class BeliefMaintError(ValueError):
    pass


def validate_transition_matrix(matrix: np.ndarray) -> np.ndarray:
    p = np.asarray(matrix, dtype=float)
    if p.shape != (4, 4):
        raise BeliefMaintError("transition matrix must be 4x4 for healthy/degraded/critical/failed")
    if np.any(p < -1e-12):
        raise BeliefMaintError("transition probabilities cannot be negative")
    if not np.allclose(p.sum(axis=1), 1.0, atol=1e-10):
        raise BeliefMaintError("transition matrix rows must sum to 1")
    if not np.allclose(p[FAILED_INDEX], np.array([0.0, 0.0, 0.0, 1.0]), atol=1e-10):
        raise BeliefMaintError("failed state must be absorbing")
    return p


def validate_assets(assets: Iterable[BeliefMaintAsset], horizon: int) -> list[BeliefMaintAsset]:
    items = list(assets)
    if horizon <= 0:
        raise BeliefMaintError("horizon must be positive")
    if not items:
        raise BeliefMaintError("at least one asset is required")
    ids = [x.asset_id for x in items]
    if len(ids) != len(set(ids)):
        raise BeliefMaintError("asset_id values must be unique")
    for asset in items:
        b = np.asarray(asset.initial_belief, dtype=float)
        if b.shape != (4,) or np.any(b < -1e-12) or not np.isclose(b.sum(), 1.0, atol=1e-10):
            raise BeliefMaintError(f"asset {asset.asset_id} initial belief must be a probability vector of length 4")
        if len(asset.opportunity_cost_by_cycle) != horizon:
            raise BeliefMaintError(f"asset {asset.asset_id} opportunity-cost vector must match horizon")
        if asset.preventive_cost < 0 or asset.failure_cost < 0 or min(asset.opportunity_cost_by_cycle) < 0:
            raise BeliefMaintError("costs cannot be negative")
    return items


def propagate_beliefs(
    assets: Iterable[BeliefMaintAsset], transition_matrix: np.ndarray, horizon: int
) -> dict[int, np.ndarray]:
    items = validate_assets(assets, horizon)
    p = validate_transition_matrix(transition_matrix)
    output: dict[int, np.ndarray] = {}
    for asset in items:
        history = np.zeros((horizon + 1, 4), dtype=float)
        history[0] = np.asarray(asset.initial_belief, dtype=float)
        for t in range(1, horizon + 1):
            history[t] = history[t - 1] @ p
        output[asset.asset_id] = history
    return output


def build_cost_matrix(
    assets: Iterable[BeliefMaintAsset], transition_matrix: np.ndarray, horizon: int
) -> tuple[list[BeliefMaintAsset], dict[int, np.ndarray], np.ndarray]:
    items = validate_assets(assets, horizon)
    beliefs = propagate_beliefs(items, transition_matrix, horizon)
    costs = np.zeros((len(items), horizon), dtype=float)
    for i, asset in enumerate(items):
        failed_prob = beliefs[asset.asset_id][:, FAILED_INDEX]
        for cycle in range(1, horizon + 1):
            # Failed is absorbing, so the sum of P(failed by t) is an expected failed-cycle exposure proxy.
            exposure = float(failed_prob[1 : cycle + 1].sum())
            costs[i, cycle - 1] = (
                asset.preventive_cost
                + asset.failure_cost * exposure
                + float(asset.opportunity_cost_by_cycle[cycle - 1])
            )
    return items, beliefs, costs


def _normalize_capacity(horizon: int, crew_capacity_by_cycle: int | Iterable[int]) -> np.ndarray:
    if isinstance(crew_capacity_by_cycle, int):
        cap = np.full(horizon, crew_capacity_by_cycle, dtype=int)
    else:
        cap = np.asarray(list(crew_capacity_by_cycle), dtype=int)
    if cap.shape != (horizon,) or np.any(cap <= 0):
        raise BeliefMaintError("crew capacity must be positive for every cycle in the horizon")
    return cap


def solve_belief_maint(
    assets: Iterable[BeliefMaintAsset],
    transition_matrix: np.ndarray,
    *,
    horizon: int,
    crew_capacity_by_cycle: int | Iterable[int] = 1,
) -> BeliefMaintResult:
    items, beliefs, costs = build_cost_matrix(assets, transition_matrix, horizon)
    p = validate_transition_matrix(transition_matrix)
    cap = _normalize_capacity(horizon, crew_capacity_by_cycle)
    n = len(items)

    c = costs.reshape(-1)
    integrality = np.ones(n * horizon, dtype=int)
    lower = np.zeros(n * horizon)
    upper = np.ones(n * horizon)

    # Every asset receives exactly one intervention timing decision.
    aeq = np.zeros((n, n * horizon))
    for i in range(n):
        aeq[i, i * horizon : (i + 1) * horizon] = 1.0

    # Crew capacity limits concurrent interventions by cycle.
    acap = np.zeros((horizon, n * horizon))
    for t in range(horizon):
        for i in range(n):
            acap[t, i * horizon + t] = 1.0

    result = milp(
        c=c,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=[
            LinearConstraint(aeq, np.ones(n), np.ones(n)),
            LinearConstraint(acap, -np.inf, cap.astype(float)),
        ],
        options={"disp": False},
    )
    if not result.success or result.x is None:
        return BeliefMaintResult(
            algorithm="BELIEF-MAINT",
            status="INFEASIBLE",
            solver="SciPy-HiGHS MILP",
            objective_value=float("inf"),
            schedule=(),
            transition_matrix=tuple(tuple(float(v) for v in row) for row in p),
            horizon=horizon,
            crew_capacity_by_cycle=tuple(int(x) for x in cap),
            claim_boundary="No operational claim: the modeled maintenance problem was infeasible.",
        )

    x = result.x.reshape(n, horizon)
    schedule: list[BeliefMaintSchedule] = []
    for i, asset in enumerate(items):
        cycle = int(np.argmax(x[i])) + 1
        failed_prob = beliefs[asset.asset_id][:, FAILED_INDEX]
        exposure = float(failed_prob[1 : cycle + 1].sum())
        schedule.append(
            BeliefMaintSchedule(
                asset_id=asset.asset_id,
                maintenance_cycle=cycle,
                failure_probability_at_maintenance=float(failed_prob[cycle]),
                cumulative_failure_exposure=exposure,
                opportunity_cost=float(asset.opportunity_cost_by_cycle[cycle - 1]),
                modeled_cost=float(costs[i, cycle - 1]),
            )
        )
    schedule.sort(key=lambda row: (row.maintenance_cycle, row.asset_id))
    return BeliefMaintResult(
        algorithm="BELIEF-MAINT",
        status="OPTIMAL",
        solver="SciPy-HiGHS MILP",
        objective_value=float(result.fun),
        schedule=tuple(schedule),
        transition_matrix=tuple(tuple(float(v) for v in row) for row in p),
        horizon=horizon,
        crew_capacity_by_cycle=tuple(int(x) for x in cap),
        claim_boundary=(
            "BELIEF-MAINT is a model-based maintenance-timing decision under declared degradation transitions, "
            "costs and crew capacity. Belief transitions are not causal proof and the schedule is not a guaranteed "
            "failure-prevention outcome."
        ),
    )


def evaluate_schedule_objective(
    assets: Iterable[BeliefMaintAsset],
    transition_matrix: np.ndarray,
    *,
    horizon: int,
    maintenance_cycle_by_asset: dict[int, int],
) -> float:
    items, _, costs = build_cost_matrix(assets, transition_matrix, horizon)
    total = 0.0
    for i, asset in enumerate(items):
        cycle = int(maintenance_cycle_by_asset[asset.asset_id])
        if not 1 <= cycle <= horizon:
            raise BeliefMaintError("baseline maintenance cycle lies outside the horizon")
        total += float(costs[i, cycle - 1])
    return total


def _assign_with_capacity(preferred: list[tuple[int, list[int]]], cap: np.ndarray) -> dict[int, int]:
    remaining = cap.astype(int).copy()
    schedule: dict[int, int] = {}
    for asset_id, cycle_order in preferred:
        chosen = next((cycle for cycle in cycle_order if remaining[cycle - 1] > 0), None)
        if chosen is None:
            raise BeliefMaintError("baseline cannot fit within crew capacity")
        schedule[asset_id] = chosen
        remaining[chosen - 1] -= 1
    return schedule


def baseline_schedules(
    assets: Iterable[BeliefMaintAsset],
    transition_matrix: np.ndarray,
    *,
    horizon: int,
    crew_capacity_by_cycle: int | Iterable[int] = 1,
) -> dict[str, dict]:
    items, _, _ = build_cost_matrix(assets, transition_matrix, horizon)
    cap = _normalize_capacity(horizon, crew_capacity_by_cycle)
    if int(cap.sum()) < len(items):
        raise BeliefMaintError("not enough crew slots for baseline schedules")

    cycles = list(range(1, horizon + 1))

    # Fixed interval: asset order, middle-to-late horizon target.
    target = max(1, min(horizon, round(0.7 * horizon)))
    fixed_order = sorted(cycles, key=lambda t: (abs(t - target), t))
    fixed_pref = [(a.asset_id, fixed_order) for a in sorted(items, key=lambda x: x.asset_id)]
    fixed = _assign_with_capacity(fixed_pref, cap)

    # Risk rank: current critical+failed belief gets scarce early crew slots first.
    ranked = sorted(
        items,
        key=lambda a: (
            -(float(a.initial_belief[2]) + float(a.initial_belief[3])),
            a.asset_id,
        ),
    )
    risk_pref = [(a.asset_id, cycles) for a in ranked]
    risk_rank = _assign_with_capacity(risk_pref, cap)

    # Lowest-load heuristic: ignore degradation belief and chase each asset's cheapest production window.
    load_pref = []
    for a in sorted(items, key=lambda x: x.asset_id):
        ordered = sorted(cycles, key=lambda t: (a.opportunity_cost_by_cycle[t - 1], t))
        load_pref.append((a.asset_id, ordered))
    lowest_load = _assign_with_capacity(load_pref, cap)

    out: dict[str, dict] = {}
    for name, sched in {
        "fixed_interval": fixed,
        "risk_rank": risk_rank,
        "lowest_production_load": lowest_load,
    }.items():
        out[name] = {
            "maintenance_cycle_by_asset": sched,
            "objective_value": evaluate_schedule_objective(
                items, transition_matrix, horizon=horizon, maintenance_cycle_by_asset=sched
            ),
        }
    return out


def stress_transition_matrix(transition_matrix: np.ndarray, factor: float) -> np.ndarray:
    """Increase probability of moving to worse states while preserving a row-stochastic absorbing chain."""
    if factor <= 0:
        raise BeliefMaintError("stress factor must be positive")
    p = validate_transition_matrix(transition_matrix).copy()
    out = np.zeros_like(p)
    for i in range(FAILED_INDEX):
        worse = p[i, i + 1 :].copy() * factor
        worse_sum = float(worse.sum())
        if worse_sum >= 1.0:
            worse = worse / worse_sum * 0.999
            worse_sum = float(worse.sum())
        out[i, i] = 1.0 - worse_sum
        out[i, i + 1 :] = worse
    out[FAILED_INDEX, FAILED_INDEX] = 1.0
    return validate_transition_matrix(out)


def make_reference_problem() -> tuple[list[BeliefMaintAsset], np.ndarray, int, tuple[int, ...]]:
    horizon = 6
    transition = np.array(
        [
            [0.84, 0.13, 0.03, 0.00],
            [0.00, 0.64, 0.27, 0.09],
            [0.00, 0.00, 0.56, 0.44],
            [0.00, 0.00, 0.00, 1.00],
        ],
        dtype=float,
    )
    assets = [
        BeliefMaintAsset(101, (0.08, 0.22, 0.62, 0.08), 5_500, 42_000, (18_000, 1_500, 2_000, 3_000, 4_500, 5_000)),
        BeliefMaintAsset(102, (0.35, 0.45, 0.18, 0.02), 5_000, 36_000, (1_800, 2_200, 6_000, 5_000, 3_000, 2_000)),
        BeliefMaintAsset(103, (0.60, 0.30, 0.09, 0.01), 4_800, 32_000, (2_000, 1_500, 1_200, 5_000, 4_000, 2_500)),
        BeliefMaintAsset(104, (0.20, 0.42, 0.34, 0.04), 5_200, 39_000, (5_500, 2_500, 2_000, 1_800, 4_500, 3_500)),
        BeliefMaintAsset(105, (0.72, 0.22, 0.05, 0.01), 4_500, 28_000, (1_000, 4_500, 4_000, 2_000, 1_400, 1_200)),
    ]
    return assets, transition, horizon, (1, 1, 1, 1, 1, 1)
