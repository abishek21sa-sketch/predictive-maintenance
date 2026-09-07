from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from time import perf_counter
from typing import Literal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


@dataclass(frozen=True)
class AssetMaintenanceInput:
    asset_id: int
    predicted_rul: float
    failure_probability: float
    preventive_cost: float = 5000.0
    failure_cost: float = 30000.0
    downtime_cost_per_cycle: float = 1200.0
    duration: int = 1
    labor_hours: float = 8.0
    spare_units: int = 1
    required_skill: str = "mechanic"
    part_id: str = "generic"


@dataclass(frozen=True)
class ScheduledMaintenance:
    asset_id: int
    cycle: int
    expected_cost: float
    duration: int = 1
    required_skill: str = "mechanic"
    part_id: str = "generic"


@dataclass(frozen=True)
class SolverEvidence:
    solver: str
    status: str
    objective_value: float
    best_bound: float | None
    mip_gap: float | None
    solve_time_seconds: float
    termination_reason: str

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class MaintenancePlanResult:
    schedule: list[ScheduledMaintenance]
    evidence: SolverEvidence

    @property
    def objective_value(self) -> float:
        return self.evidence.objective_value


def _validate_inputs(inputs: list[AssetMaintenanceInput], horizon: int, capacity_per_cycle: int) -> None:
    if horizon <= 0 or capacity_per_cycle <= 0:
        raise ValueError("horizon and capacity_per_cycle must be positive")
    ids = [x.asset_id for x in inputs]
    if len(ids) != len(set(ids)):
        raise ValueError("asset_id values must be unique")
    for item in inputs:
        if item.duration <= 0 or item.duration > horizon:
            raise ValueError(f"Invalid maintenance duration for asset {item.asset_id}")
        if item.labor_hours < 0 or item.spare_units < 0:
            raise ValueError("labor_hours and spare_units cannot be negative")
        if not 0 <= item.failure_probability <= 1:
            raise ValueError("failure_probability must be in [0, 1]")


def _cost(item: AssetMaintenanceInput, start: int, rul_override: float | None = None) -> float:
    rul = float(item.predicted_rul if rul_override is None else rul_override)
    finish = start + item.duration - 1
    lateness = max(0.0, finish - rul)
    target = max(1.0, 0.60 * rul)
    dynamic_risk = max(item.failure_probability, 1.0 / (1.0 + np.exp((rul - 30.0) / 7.5)))
    expected_failure = dynamic_risk * item.failure_cost * (1.0 + 0.20 * lateness)
    early_life_loss = 120.0 * max(0.0, target - start)
    late_risk_penalty = item.downtime_cost_per_cycle * max(0.0, finish - target)
    maintenance_downtime = item.downtime_cost_per_cycle * item.duration
    return float(item.preventive_cost + expected_failure + early_life_loss + late_risk_penalty + maintenance_downtime)


def _scenario_costs(
    inputs: list[AssetMaintenanceInput], horizon: int,
    rul_scenarios: list[dict[int, float]] | None = None,
    probabilities: list[float] | None = None,
) -> np.ndarray:
    scenarios = rul_scenarios or []
    if scenarios:
        probs = np.asarray(probabilities if probabilities is not None else np.ones(len(scenarios)), dtype=float)
        if len(probs) != len(scenarios) or np.any(probs < 0) or probs.sum() <= 0:
            raise ValueError("Scenario probabilities must be non-negative and match scenarios")
        probs = probs / probs.sum()
    else:
        probs = np.array([])

    costs = np.full((len(inputs), horizon), 1e12, dtype=float)
    for i, item in enumerate(inputs):
        latest_start = horizon - item.duration + 1
        for start in range(1, latest_start + 1):
            if not scenarios:
                costs[i, start - 1] = _cost(item, start)
            else:
                costs[i, start - 1] = float(sum(
                    p * _cost(item, start, scenario.get(item.asset_id, item.predicted_rul))
                    for p, scenario in zip(probs, scenarios)
                ))
    return costs


def _occupies(start_index: int, duration: int, cycle_index: int) -> bool:
    return start_index <= cycle_index < start_index + duration


def _scipy_constraints(
    inputs: list[AssetMaintenanceInput], horizon: int, capacity_per_cycle: int,
    labor_hours_per_cycle: float | None, spares_per_cycle: int | None,
    skill_capacity_per_cycle: dict[str, int] | None,
    part_inventory: dict[str, int] | None,
) -> list[LinearConstraint]:
    n, h = len(inputs), horizon
    Aeq = np.zeros((n, n * h))
    for i, item in enumerate(inputs):
        Aeq[i, i * h : i * h + (h - item.duration + 1)] = 1
    constraints: list[LinearConstraint] = [LinearConstraint(Aeq, np.ones(n), np.ones(n))]

    # Resource occupancy is enforced for every cycle touched by a maintenance job.
    Ashop = np.zeros((h, n * h))
    Alabor = np.zeros((h, n * h)) if labor_hours_per_cycle is not None else None
    Aspares = np.zeros((h, n * h)) if spares_per_cycle is not None else None
    skill_mats = {skill: np.zeros((h, n * h)) for skill in (skill_capacity_per_cycle or {})}

    for i, item in enumerate(inputs):
        latest_start = h - item.duration
        per_cycle_labor = item.labor_hours / item.duration
        for s in range(latest_start + 1):
            for t in range(h):
                if _occupies(s, item.duration, t):
                    Ashop[t, i * h + s] = 1.0
                    if Alabor is not None:
                        Alabor[t, i * h + s] = per_cycle_labor
                    if Aspares is not None and t == s:
                        Aspares[t, i * h + s] = item.spare_units
                    if item.required_skill in skill_mats:
                        skill_mats[item.required_skill][t, i * h + s] = 1.0

    constraints.append(LinearConstraint(Ashop, -np.inf, np.full(h, float(capacity_per_cycle))))
    if Alabor is not None:
        constraints.append(LinearConstraint(Alabor, -np.inf, np.full(h, float(labor_hours_per_cycle))))
    if Aspares is not None:
        constraints.append(LinearConstraint(Aspares, -np.inf, np.full(h, int(spares_per_cycle))))
    for skill, mat in skill_mats.items():
        constraints.append(LinearConstraint(mat, -np.inf, np.full(h, int(skill_capacity_per_cycle[skill]))))

    for part, stock in (part_inventory or {}).items():
        Apart = np.zeros((1, n * h))
        for i, item in enumerate(inputs):
            if item.part_id == part:
                Apart[0, i * h : i * h + (h - item.duration + 1)] = item.spare_units
        constraints.append(LinearConstraint(Apart, -np.inf, np.array([int(stock)])))
    return constraints


def _solve_scipy(
    inputs: list[AssetMaintenanceInput], costs: np.ndarray, horizon: int, capacity_per_cycle: int,
    labor_hours_per_cycle: float | None, spares_per_cycle: int | None,
    skill_capacity_per_cycle: dict[str, int] | None, part_inventory: dict[str, int] | None,
) -> MaintenancePlanResult:
    n, h = len(inputs), horizon
    c = costs.reshape(-1)
    constraints = _scipy_constraints(
        inputs, h, capacity_per_cycle, labor_hours_per_cycle, spares_per_cycle,
        skill_capacity_per_cycle, part_inventory,
    )
    start_time = perf_counter()
    result = milp(c=c, integrality=np.ones(n * h), bounds=Bounds(0, 1), constraints=constraints)
    elapsed = perf_counter() - start_time
    if not result.success:
        raise RuntimeError(f"Maintenance MILP failed ({result.status}): {result.message}")
    x = result.x.reshape(n, h)
    schedule = []
    for i, item in enumerate(inputs):
        s = int(np.argmax(x[i]))
        schedule.append(ScheduledMaintenance(
            item.asset_id, s + 1, float(costs[i, s]), item.duration, item.required_skill, item.part_id
        ))
    gap = getattr(result, "mip_gap", None)
    evidence = SolverEvidence(
        solver="scipy_highs_milp", status="OPTIMAL", objective_value=float(result.fun),
        best_bound=None, mip_gap=None if gap is None else float(gap),
        solve_time_seconds=float(elapsed), termination_reason=str(result.message),
    )
    return MaintenancePlanResult(sorted(schedule, key=lambda s: (s.cycle, s.asset_id)), evidence)


def _solve_gurobi(
    inputs: list[AssetMaintenanceInput], costs: np.ndarray, horizon: int, capacity_per_cycle: int,
    labor_hours_per_cycle: float | None, spares_per_cycle: int | None,
    skill_capacity_per_cycle: dict[str, int] | None, part_inventory: dict[str, int] | None,
    time_limit_seconds: float | None = 60.0,
) -> MaintenancePlanResult:
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except ImportError as exc:
        raise RuntimeError("Gurobi requested but gurobipy is not installed") from exc

    h = horizon
    m = gp.Model("predictive_maintenance_intervention_planner")
    m.Params.OutputFlag = 0
    if time_limit_seconds is not None:
        m.Params.TimeLimit = float(time_limit_seconds)
    x = {}
    for i, item in enumerate(inputs):
        for s in range(h - item.duration + 1):
            x[i, s] = m.addVar(vtype=GRB.BINARY, name=f"x_{i}_{s}")
    m.setObjective(gp.quicksum(costs[i, s] * var for (i, s), var in x.items()), GRB.MINIMIZE)

    for i, item in enumerate(inputs):
        m.addConstr(gp.quicksum(x[i, s] for s in range(h - item.duration + 1)) == 1, name=f"assign_{i}")
    for t in range(h):
        active = [(i, s, var) for (i, s), var in x.items() if _occupies(s, inputs[i].duration, t)]
        m.addConstr(gp.quicksum(var for _, _, var in active) <= capacity_per_cycle, name=f"shop_{t}")
        if labor_hours_per_cycle is not None:
            m.addConstr(gp.quicksum((inputs[i].labor_hours / inputs[i].duration) * var for i, _, var in active)
                        <= labor_hours_per_cycle, name=f"labor_{t}")
        if spares_per_cycle is not None:
            starts = [(i, s, var) for (i, s), var in x.items() if s == t]
            m.addConstr(gp.quicksum(inputs[i].spare_units * var for i, _, var in starts)
                        <= spares_per_cycle, name=f"spares_{t}")
        for skill, cap in (skill_capacity_per_cycle or {}).items():
            m.addConstr(gp.quicksum(var for i, _, var in active if inputs[i].required_skill == skill)
                        <= cap, name=f"skill_{skill}_{t}")
    for part, stock in (part_inventory or {}).items():
        m.addConstr(gp.quicksum(inputs[i].spare_units * var for (i, _), var in x.items() if inputs[i].part_id == part)
                    <= stock, name=f"inventory_{part}")

    m.optimize()
    status_map = {
        GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "TIME_LIMIT", GRB.INFEASIBLE: "INFEASIBLE",
        GRB.UNBOUNDED: "UNBOUNDED", GRB.INF_OR_UNBD: "INF_OR_UNBD", GRB.INTERRUPTED: "INTERRUPTED",
    }
    status = status_map.get(m.Status, f"STATUS_{m.Status}")
    if m.SolCount == 0:
        raise RuntimeError(f"Gurobi maintenance MILP returned {status} with no incumbent solution")
    schedule = []
    for (i, s), var in x.items():
        if var.X > 0.5:
            item = inputs[i]
            schedule.append(ScheduledMaintenance(
                item.asset_id, s + 1, float(costs[i, s]), item.duration, item.required_skill, item.part_id
            ))
    evidence = SolverEvidence(
        solver="gurobi", status=status, objective_value=float(m.ObjVal),
        best_bound=float(m.ObjBound), mip_gap=float(m.MIPGap) if m.IsMIP else 0.0,
        solve_time_seconds=float(m.Runtime), termination_reason=status,
    )
    return MaintenancePlanResult(sorted(schedule, key=lambda s: (s.cycle, s.asset_id)), evidence)


def solve_maintenance(
    inputs: list[AssetMaintenanceInput], horizon: int = 30, capacity_per_cycle: int = 2,
    labor_hours_per_cycle: float | None = None, spares_per_cycle: int | None = None,
    skill_capacity_per_cycle: dict[str, int] | None = None, part_inventory: dict[str, int] | None = None,
    rul_scenarios: list[dict[int, float]] | None = None, probabilities: list[float] | None = None,
    solver: Literal["auto", "gurobi", "highs"] = "auto",
) -> MaintenancePlanResult:
    if not inputs:
        return MaintenancePlanResult([], SolverEvidence("none", "OPTIMAL", 0.0, 0.0, 0.0, 0.0, "empty problem"))
    _validate_inputs(inputs, horizon, capacity_per_cycle)
    costs = _scenario_costs(inputs, horizon, rul_scenarios, probabilities)
    if solver in ("auto", "gurobi"):
        try:
            return _solve_gurobi(inputs, costs, horizon, capacity_per_cycle, labor_hours_per_cycle,
                                 spares_per_cycle, skill_capacity_per_cycle, part_inventory)
        except (RuntimeError, Exception) as exc:
            if solver == "gurobi":
                raise
            # In auto mode, missing/unlicensed Gurobi cleanly falls back to HiGHS.
            if exc.__class__.__name__ not in {"GurobiError", "RuntimeError", "ModuleNotFoundError", "ImportError"}:
                raise
    return _solve_scipy(inputs, costs, horizon, capacity_per_cycle, labor_hours_per_cycle,
                        spares_per_cycle, skill_capacity_per_cycle, part_inventory)


def optimize_maintenance(
    inputs: list[AssetMaintenanceInput], horizon: int = 30, capacity_per_cycle: int = 2,
    labor_hours_per_cycle: float | None = None, spares_per_cycle: int | None = None,
    skill_capacity_per_cycle: dict[str, int] | None = None, part_inventory: dict[str, int] | None = None,
) -> list[ScheduledMaintenance]:
    return solve_maintenance(inputs, horizon, capacity_per_cycle, labor_hours_per_cycle, spares_per_cycle,
                             skill_capacity_per_cycle, part_inventory).schedule


def optimize_maintenance_stochastic(
    inputs: list[AssetMaintenanceInput], rul_scenarios: list[dict[int, float]], probabilities: list[float] | None = None,
    horizon: int = 30, capacity_per_cycle: int = 2, labor_hours_per_cycle: float | None = None,
    spares_per_cycle: int | None = None, skill_capacity_per_cycle: dict[str, int] | None = None,
    part_inventory: dict[str, int] | None = None,
) -> list[ScheduledMaintenance]:
    return solve_maintenance(inputs, horizon, capacity_per_cycle, labor_hours_per_cycle, spares_per_cycle,
                             skill_capacity_per_cycle, part_inventory, rul_scenarios, probabilities).schedule


def validate_schedule(
    schedule: list[ScheduledMaintenance], inputs: list[AssetMaintenanceInput], horizon: int,
    capacity_per_cycle: int, labor_hours_per_cycle: float | None = None,
    skill_capacity_per_cycle: dict[str, int] | None = None,
) -> dict[str, object]:
    by_id = {x.asset_id: x for x in inputs}
    violations: list[str] = []
    if {x.asset_id for x in schedule} != set(by_id):
        violations.append("assignment_mismatch")
    for t in range(1, horizon + 1):
        active = [s for s in schedule if s.cycle <= t < s.cycle + s.duration]
        if len(active) > capacity_per_cycle:
            violations.append(f"shop_capacity_cycle_{t}")
        if labor_hours_per_cycle is not None:
            labor = sum(by_id[s.asset_id].labor_hours / by_id[s.asset_id].duration for s in active)
            if labor > labor_hours_per_cycle + 1e-8:
                violations.append(f"labor_cycle_{t}")
        for skill, cap in (skill_capacity_per_cycle or {}).items():
            used = sum(1 for s in active if by_id[s.asset_id].required_skill == skill)
            if used > cap:
                violations.append(f"skill_{skill}_cycle_{t}")
    return {"feasible": not violations, "violations": violations}


def enumerate_small_instance(
    inputs: list[AssetMaintenanceInput], horizon: int, capacity_per_cycle: int,
) -> tuple[float, list[ScheduledMaintenance]]:
    """Exact oracle for tiny deterministic instances; intentionally exponential and test-only."""
    _validate_inputs(inputs, horizon, capacity_per_cycle)
    costs = _scenario_costs(inputs, horizon)
    starts = [range(1, horizon - item.duration + 2) for item in inputs]
    best_cost = float("inf")
    best: list[ScheduledMaintenance] = []
    for combo in product(*starts):
        candidate = [ScheduledMaintenance(item.asset_id, s, costs[i, s - 1], item.duration,
                                          item.required_skill, item.part_id)
                     for i, (item, s) in enumerate(zip(inputs, combo))]
        if not validate_schedule(candidate, inputs, horizon, capacity_per_cycle)["feasible"]:
            continue
        obj = float(sum(x.expected_cost for x in candidate))
        if obj < best_cost:
            best_cost, best = obj, candidate
    if not best:
        raise RuntimeError("No feasible schedule in enumeration oracle")
    return best_cost, sorted(best, key=lambda s: (s.cycle, s.asset_id))
