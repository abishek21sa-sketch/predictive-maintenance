from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from itertools import product
from math import exp
from time import perf_counter
from typing import Literal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from pdm_intelligence.optimization.maintenance import AssetMaintenanceInput, SolverEvidence

ActionName = Literal["maintain", "inspect", "defer"]


@dataclass(frozen=True)
class InterventionAssumptions:
    inspection_cost: float = 2200.0
    inspection_duration: int = 1
    inspection_labor_hours: float = 4.0
    inspection_followup_fraction: float = 0.65
    inspection_failure_fraction: float = 0.20
    defer_cycles: int = 5
    emergency_penalty: float = 12000.0
    criticality_multiplier: float = 1.0


@dataclass(frozen=True)
class CapacityProfile:
    name: str
    bay_capacity: tuple[int, ...]
    labor_hours: tuple[float, ...]
    skill_capacity: dict[str, tuple[int, ...]]
    part_inventory: dict[str, int]

    def validate(self, horizon: int) -> None:
        if len(self.bay_capacity) != horizon or len(self.labor_hours) != horizon:
            raise ValueError(f"Capacity profile {self.name} must have {horizon} cycles")
        if any(x < 0 for x in self.bay_capacity) or any(x < 0 for x in self.labor_hours):
            raise ValueError("Capacity values cannot be negative")
        for skill, values in self.skill_capacity.items():
            if len(values) != horizon or any(x < 0 for x in values):
                raise ValueError(f"Skill profile {skill} must have non-negative values for each cycle")
        if any(v < 0 for v in self.part_inventory.values()):
            raise ValueError("Part inventory cannot be negative")


@dataclass(frozen=True)
class InterventionChoice:
    asset_id: int
    action: ActionName
    start_cycle: int | None
    end_cycle: int | None
    duration: int
    expected_cost: float
    required_skill: str | None
    part_id: str | None
    labor_hours: float

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class RiskEvidence:
    expected_cost: float
    cvar_alpha: float
    cvar_cost: float
    risk_aversion: float
    objective_value: float
    scenario_costs: tuple[float, ...]
    scenario_probabilities: tuple[float, ...]

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class InterventionPlanResult:
    choices: list[InterventionChoice]
    evidence: SolverEvidence
    risk: RiskEvidence
    capacity_profiles: tuple[str, ...]

    def to_dict(self):
        return {
            "choices": [x.to_dict() for x in self.choices],
            "solver_evidence": self.evidence.to_dict(),
            "risk_evidence": self.risk.to_dict(),
            "capacity_profiles": list(self.capacity_profiles),
        }


@dataclass(frozen=True)
class _Candidate:
    asset_index: int
    asset_id: int
    action: ActionName
    start_index: int | None
    duration: int
    labor_hours: float
    skill: str | None
    part_id: str | None
    spare_units: int


@dataclass(frozen=True)
class FeasibilityDiagnostic:
    feasible_by_precheck: bool
    reasons: tuple[str, ...]
    profile_summaries: tuple[dict, ...]

    def to_dict(self):
        return asdict(self)


def default_capacity_profile(
    horizon: int,
    *,
    name: str = "baseline",
    bays: int = 2,
    labor_hours_per_cycle: float = 24.0,
    skill_capacity: dict[str, int] | None = None,
    part_inventory: dict[str, int] | None = None,
) -> CapacityProfile:
    skills = skill_capacity or {"mechanic": max(1, bays), "senior_mechanic": max(1, bays)}
    return CapacityProfile(
        name=name,
        bay_capacity=tuple(int(bays) for _ in range(horizon)),
        labor_hours=tuple(float(labor_hours_per_cycle) for _ in range(horizon)),
        skill_capacity={k: tuple(int(v) for _ in range(horizon)) for k, v in skills.items()},
        part_inventory={k: int(v) for k, v in (part_inventory or {}).items()},
    )


def failure_exposure_probability(base_probability: float, rul: float, delay_cycles: float) -> float:
    """Monotone delay exposure model used only for decision economics, not a calibrated probability model."""
    if not 0 <= base_probability <= 1:
        raise ValueError("base_probability must be in [0, 1]")
    if rul <= 0 or delay_cycles < 0:
        raise ValueError("rul must be positive and delay_cycles non-negative")
    survival_of_increment = exp(-float(delay_cycles) / max(float(rul), 1e-9))
    return float(min(1.0, max(0.0, 1.0 - (1.0 - base_probability) * survival_of_increment)))


def _pre_intervention_failure_probability(base_probability: float, rul: float, delay_cycles: float) -> float:
    """Modeled probability of failure before an intervention after ``delay_cycles``.

    ``base_probability`` is treated as an engineering risk input rather than a calibrated
    one-cycle probability. The transformation is monotone, is zero for immediate action,
    and increases as delay consumes a larger share of predicted RUL.
    """
    if not 0 <= base_probability <= 1:
        raise ValueError("base_probability must be in [0, 1]")
    if rul <= 0 or delay_cycles < 0:
        raise ValueError("rul must be positive and delay_cycles non-negative")
    if delay_cycles == 0 or base_probability == 0:
        return 0.0
    exponent = float(delay_cycles) / max(float(rul), 1e-9)
    return float(min(1.0, max(0.0, 1.0 - (1.0 - base_probability) ** exponent)))


def _action_cost(
    item: AssetMaintenanceInput,
    assumptions: InterventionAssumptions,
    action: ActionName,
    start_cycle: int | None,
    scenario_rul: float,
) -> float:
    criticality = max(0.1, float(assumptions.criticality_multiplier))
    rul = max(float(scenario_rul), 0.1)
    if action == "maintain":
        assert start_cycle is not None
        delay = max(0.0, start_cycle - 1.0)
        exposure = _pre_intervention_failure_probability(item.failure_probability, rul, delay)
        expected_failure = exposure * (item.failure_cost + assumptions.emergency_penalty) * criticality
        expected_failure_downtime = exposure * max(3.0, item.duration * 2.0) * item.downtime_cost_per_cycle * criticality
        planned_downtime = item.duration * item.downtime_cost_per_cycle
        early_life_loss = 100.0 * max(0.0, 0.50 * rul - start_cycle)
        return float(item.preventive_cost + planned_downtime + expected_failure + expected_failure_downtime + early_life_loss)
    if action == "inspect":
        assert start_cycle is not None
        delay = max(0.0, start_cycle - 1.0)
        pre_inspection_failure = _pre_intervention_failure_probability(item.failure_probability, rul, delay)
        # Inspection buys information, not physical risk reduction. A positive finding triggers
        # an expected follow-up maintenance burden; residual miss risk remains explicit.
        positive_finding_probability = min(0.98, 0.20 + 0.80 * item.failure_probability)
        followup_pm = positive_finding_probability * (item.preventive_cost + item.duration * item.downtime_cost_per_cycle)
        miss_risk = assumptions.inspection_failure_fraction * item.failure_probability
        residual_failure = miss_risk * (item.failure_cost + assumptions.emergency_penalty) * criticality
        pre_failure_cost = pre_inspection_failure * (item.failure_cost + assumptions.emergency_penalty) * criticality
        downtime = assumptions.inspection_duration * item.downtime_cost_per_cycle * 0.5
        return float(assumptions.inspection_cost + downtime + followup_pm + residual_failure + pre_failure_cost)
    if action == "defer":
        delay = float(assumptions.defer_cycles)
        exposure = _pre_intervention_failure_probability(item.failure_probability, rul, delay)
        expected_failure = exposure * (item.failure_cost + assumptions.emergency_penalty) * criticality
        expected_downtime = exposure * max(3.0, item.duration * 2.0) * item.downtime_cost_per_cycle * criticality
        deferred_pm = (0.98 ** delay) * (item.preventive_cost + item.duration * item.downtime_cost_per_cycle)
        return float(deferred_pm + expected_failure + expected_downtime)
    raise ValueError(f"Unknown action {action}")


def _build_candidates(
    items: list[AssetMaintenanceInput],
    assumptions_by_asset: dict[int, InterventionAssumptions],
    horizon: int,
    allow_defer: bool,
    critical_rul_threshold: float,
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for i, item in enumerate(items):
        a = assumptions_by_asset[item.asset_id]
        for s in range(horizon - item.duration + 1):
            candidates.append(_Candidate(i, item.asset_id, "maintain", s, item.duration,
                                         item.labor_hours, item.required_skill, item.part_id, item.spare_units))
        for s in range(horizon - a.inspection_duration + 1):
            candidates.append(_Candidate(i, item.asset_id, "inspect", s, a.inspection_duration,
                                         a.inspection_labor_hours, item.required_skill, "inspection_kit", 1))
        if allow_defer and item.predicted_rul > critical_rul_threshold:
            candidates.append(_Candidate(i, item.asset_id, "defer", None, 0, 0.0, None, None, 0))
    return candidates


def _normalize_scenarios(
    items: list[AssetMaintenanceInput],
    rul_scenarios: list[dict[int, float]] | None,
    probabilities: list[float] | None,
) -> tuple[list[dict[int, float]], np.ndarray]:
    scenarios = rul_scenarios or [{x.asset_id: x.predicted_rul for x in items}]
    probs = np.asarray(probabilities if probabilities is not None else np.ones(len(scenarios)), dtype=float)
    if len(probs) != len(scenarios) or np.any(probs < 0) or probs.sum() <= 0:
        raise ValueError("Scenario probabilities must be non-negative, positive in total, and match scenarios")
    probs = probs / probs.sum()
    return scenarios, probs


def _candidate_costs(
    items: list[AssetMaintenanceInput],
    assumptions_by_asset: dict[int, InterventionAssumptions],
    candidates: list[_Candidate],
    scenarios: list[dict[int, float]],
) -> np.ndarray:
    matrix = np.zeros((len(scenarios), len(candidates)), dtype=float)
    for q, scenario in enumerate(scenarios):
        for j, c in enumerate(candidates):
            item = items[c.asset_index]
            a = assumptions_by_asset[item.asset_id]
            start = None if c.start_index is None else c.start_index + 1
            matrix[q, j] = _action_cost(item, a, c.action, start, scenario.get(item.asset_id, item.predicted_rul))
    return matrix


def _active(c: _Candidate, cycle_index: int) -> bool:
    return c.start_index is not None and c.start_index <= cycle_index < c.start_index + c.duration


def _starts(c: _Candidate, cycle_index: int) -> bool:
    return c.start_index == cycle_index


def precheck_intervention_feasibility(
    items: list[AssetMaintenanceInput],
    profiles: list[CapacityProfile],
    horizon: int,
    *,
    assumptions_by_asset: dict[int, InterventionAssumptions] | None = None,
    allow_defer: bool = True,
    critical_rul_threshold: float = 15.0,
) -> FeasibilityDiagnostic:
    assumptions = assumptions_by_asset or {x.asset_id: InterventionAssumptions() for x in items}
    reasons: list[str] = []
    for p in profiles:
        p.validate(horizon)
    mandatory = [x for x in items if not allow_defer or x.predicted_rul <= critical_rul_threshold]
    for item in mandatory:
        a = assumptions[item.asset_id]
        possible = False
        for p in profiles:
            max_bay = max(p.bay_capacity, default=0)
            max_labor = max(p.labor_hours, default=0.0)
            max_skill = max(p.skill_capacity.get(item.required_skill, (0,)), default=0)
            part_ok = p.part_inventory.get(item.part_id, item.spare_units) >= item.spare_units
            inspection_part_ok = p.part_inventory.get("inspection_kit", 1) >= 1
            possible_in_profile = (
                (item.duration <= horizon and max_bay >= 1 and max_labor + 1e-9 >= item.labor_hours / item.duration and max_skill >= 1 and part_ok)
                or (a.inspection_duration <= horizon and max_bay >= 1 and max_labor + 1e-9 >= a.inspection_labor_hours / a.inspection_duration and max_skill >= 1 and inspection_part_ok)
            )
            if not possible_in_profile:
                reasons.append(f"asset_{item.asset_id}_has_no_resource_feasible_intervention_in_{p.name}")
            possible = possible or possible_in_profile
        if not possible:
            reasons.append(f"asset_{item.asset_id}_has_no_feasible_intervention")
    summaries = tuple({
        "name": p.name,
        "min_bays": min(p.bay_capacity, default=0),
        "min_labor_hours": min(p.labor_hours, default=0.0),
        "part_inventory": dict(p.part_inventory),
    } for p in profiles)
    return FeasibilityDiagnostic(not reasons, tuple(dict.fromkeys(reasons)), summaries)


def _constraint_rows(
    items: list[AssetMaintenanceInput],
    candidates: list[_Candidate],
    profiles: list[CapacityProfile],
    horizon: int,
    max_deferrals: int | None,
) -> tuple[list[np.ndarray], list[float], list[float], list[str]]:
    rows: list[np.ndarray] = []
    lb: list[float] = []
    ub: list[float] = []
    names: list[str] = []
    nvar = len(candidates)
    for i, item in enumerate(items):
        row = np.zeros(nvar)
        for j, c in enumerate(candidates):
            if c.asset_index == i:
                row[j] = 1.0
        rows.append(row); lb.append(1.0); ub.append(1.0); names.append(f"choose_asset_{item.asset_id}")
    if max_deferrals is not None:
        row = np.array([1.0 if c.action == "defer" else 0.0 for c in candidates])
        rows.append(row); lb.append(-np.inf); ub.append(float(max_deferrals)); names.append("max_deferrals")
    for p in profiles:
        for t in range(horizon):
            row = np.array([1.0 if _active(c, t) else 0.0 for c in candidates])
            rows.append(row); lb.append(-np.inf); ub.append(float(p.bay_capacity[t])); names.append(f"{p.name}_bay_{t+1}")
            row = np.array([(c.labor_hours / c.duration) if _active(c, t) and c.duration else 0.0 for c in candidates])
            rows.append(row); lb.append(-np.inf); ub.append(float(p.labor_hours[t])); names.append(f"{p.name}_labor_{t+1}")
            for skill, cap in p.skill_capacity.items():
                row = np.array([1.0 if _active(c, t) and c.skill == skill else 0.0 for c in candidates])
                rows.append(row); lb.append(-np.inf); ub.append(float(cap[t])); names.append(f"{p.name}_skill_{skill}_{t+1}")
        for part, stock in p.part_inventory.items():
            row = np.array([float(c.spare_units) if c.part_id == part and c.start_index is not None else 0.0 for c in candidates])
            rows.append(row); lb.append(-np.inf); ub.append(float(stock)); names.append(f"{p.name}_part_{part}")
    return rows, lb, ub, names


def _selected_choices(
    items: list[AssetMaintenanceInput],
    candidates: list[_Candidate],
    selected: Iterable[int],
    expected_candidate_cost: np.ndarray,
) -> list[InterventionChoice]:
    result = []
    for j in selected:
        c = candidates[j]
        start = None if c.start_index is None else c.start_index + 1
        end = None if start is None else start + c.duration - 1
        result.append(InterventionChoice(
            asset_id=c.asset_id, action=c.action, start_cycle=start, end_cycle=end, duration=c.duration,
            expected_cost=float(expected_candidate_cost[j]), required_skill=c.skill, part_id=c.part_id,
            labor_hours=float(c.labor_hours),
        ))
    return sorted(result, key=lambda x: (x.start_cycle is None, x.start_cycle or 10**9, x.asset_id))


def _risk_from_selection(
    selected: list[int],
    costs: np.ndarray,
    probs: np.ndarray,
    alpha: float,
    risk_aversion: float,
) -> RiskEvidence:
    scenario_costs = costs[:, selected].sum(axis=1) if selected else np.zeros(len(probs))
    expected = float(np.dot(probs, scenario_costs))
    order = np.argsort(scenario_costs)
    ordered_cost = scenario_costs[order]
    ordered_prob = probs[order]
    threshold = alpha
    cumulative = np.cumsum(ordered_prob)
    idx = int(np.searchsorted(cumulative, threshold, side="left"))
    eta = float(ordered_cost[min(idx, len(ordered_cost) - 1)])
    tail = eta + float(np.dot(probs, np.maximum(scenario_costs - eta, 0.0))) / max(1e-12, 1.0 - alpha)
    objective = expected + risk_aversion * tail
    return RiskEvidence(expected, alpha, float(tail), risk_aversion, float(objective),
                        tuple(float(x) for x in scenario_costs), tuple(float(x) for x in probs))


def _solve_highs(
    items: list[AssetMaintenanceInput],
    candidates: list[_Candidate],
    costs: np.ndarray,
    probs: np.ndarray,
    profiles: list[CapacityProfile],
    horizon: int,
    alpha: float,
    risk_aversion: float,
    max_deferrals: int | None,
) -> InterventionPlanResult:
    nbin = len(candidates)
    nq = len(probs)
    # Variables: candidate binaries, eta (continuous), z[q] (continuous).
    nvar = nbin + 1 + nq
    expected_candidate = probs @ costs
    c = np.zeros(nvar)
    c[:nbin] = expected_candidate
    c[nbin] = risk_aversion
    c[nbin + 1:] = risk_aversion * probs / max(1e-12, 1.0 - alpha)
    rows, lbs, ubs, _ = _constraint_rows(items, candidates, profiles, horizon, max_deferrals)
    padded_rows = [np.pad(r, (0, 1 + nq)) for r in rows]
    # z_q >= scenario_cost_q - eta => scenario_cost*x - eta - z_q <= 0
    for q in range(nq):
        row = np.zeros(nvar)
        row[:nbin] = costs[q]
        row[nbin] = -1.0
        row[nbin + 1 + q] = -1.0
        padded_rows.append(row); lbs.append(-np.inf); ubs.append(0.0)
    A = np.vstack(padded_rows)
    lower = np.zeros(nvar)
    upper = np.ones(nvar)
    lower[nbin] = -np.inf; upper[nbin] = np.inf
    upper[nbin + 1:] = np.inf
    integrality = np.zeros(nvar)
    integrality[:nbin] = 1
    start = perf_counter()
    result = milp(c=c, integrality=integrality, bounds=Bounds(lower, upper),
                  constraints=[LinearConstraint(A, np.asarray(lbs), np.asarray(ubs))])
    elapsed = perf_counter() - start
    if not result.success:
        raise RuntimeError(f"Intervention MILP failed ({result.status}): {result.message}")
    selected = [j for j in range(nbin) if result.x[j] > 0.5]
    risk = _risk_from_selection(selected, costs, probs, alpha, risk_aversion)
    choices = _selected_choices(items, candidates, selected, expected_candidate)
    gap = getattr(result, "mip_gap", None)
    evidence = SolverEvidence("scipy_highs_milp", "OPTIMAL", float(result.fun), None,
                              None if gap is None else float(gap), float(elapsed), str(result.message))
    return InterventionPlanResult(choices, evidence, risk, tuple(p.name for p in profiles))


def _solve_gurobi(
    items: list[AssetMaintenanceInput],
    candidates: list[_Candidate],
    costs: np.ndarray,
    probs: np.ndarray,
    profiles: list[CapacityProfile],
    horizon: int,
    alpha: float,
    risk_aversion: float,
    max_deferrals: int | None,
    time_limit_seconds: float = 60.0,
) -> InterventionPlanResult:
    try:
        import gurobipy as gp
        from gurobipy import GRB
    except ImportError as exc:
        raise RuntimeError("Gurobi requested but gurobipy is not installed") from exc
    m = gp.Model("pdm_uncertainty_aware_intervention")
    m.Params.OutputFlag = 0
    m.Params.TimeLimit = float(time_limit_seconds)
    x = [m.addVar(vtype=GRB.BINARY, name=f"x_{j}_{c.asset_id}_{c.action}_{c.start_index}") for j, c in enumerate(candidates)]
    eta = m.addVar(lb=-GRB.INFINITY, vtype=GRB.CONTINUOUS, name="cvar_eta")
    z = [m.addVar(lb=0.0, vtype=GRB.CONTINUOUS, name=f"cvar_excess_{q}") for q in range(len(probs))]
    expected_candidate = probs @ costs
    expected_expr = gp.quicksum(float(expected_candidate[j]) * x[j] for j in range(len(candidates)))
    cvar_expr = eta + gp.quicksum(float(probs[q]) * z[q] for q in range(len(probs))) / max(1e-12, 1.0 - alpha)
    m.setObjective(expected_expr + risk_aversion * cvar_expr, GRB.MINIMIZE)
    rows, lbs, ubs, names = _constraint_rows(items, candidates, profiles, horizon, max_deferrals)
    for row, lo, hi, name in zip(rows, lbs, ubs, names):
        expr = gp.quicksum(float(row[j]) * x[j] for j in np.nonzero(row)[0])
        if np.isfinite(lo) and np.isfinite(hi) and abs(lo - hi) <= 1e-12:
            m.addConstr(expr == float(lo), name=name)
        else:
            if np.isfinite(hi): m.addConstr(expr <= float(hi), name=name)
            if np.isfinite(lo): m.addConstr(expr >= float(lo), name=name + "_lb")
    for q in range(len(probs)):
        scenario_expr = gp.quicksum(float(costs[q, j]) * x[j] for j in range(len(candidates)))
        m.addConstr(z[q] >= scenario_expr - eta, name=f"cvar_{q}")
    m.optimize()
    status_map = {GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "TIME_LIMIT", GRB.INFEASIBLE: "INFEASIBLE",
                  GRB.UNBOUNDED: "UNBOUNDED", GRB.INF_OR_UNBD: "INF_OR_UNBD", GRB.INTERRUPTED: "INTERRUPTED"}
    status = status_map.get(m.Status, f"STATUS_{m.Status}")
    if m.SolCount == 0:
        if m.Status == GRB.INFEASIBLE:
            m.computeIIS()
            names_iis = [c.ConstrName for c in m.getConstrs() if c.IISConstr]
            raise RuntimeError(f"Gurobi intervention MILP INFEASIBLE; IIS={names_iis[:20]}")
        raise RuntimeError(f"Gurobi intervention MILP returned {status} with no incumbent")
    selected = [j for j, var in enumerate(x) if var.X > 0.5]
    risk = _risk_from_selection(selected, costs, probs, alpha, risk_aversion)
    choices = _selected_choices(items, candidates, selected, expected_candidate)
    evidence = SolverEvidence("gurobi", status, float(m.ObjVal), float(m.ObjBound),
                              float(m.MIPGap) if m.IsMIP else 0.0, float(m.Runtime), status)
    return InterventionPlanResult(choices, evidence, risk, tuple(p.name for p in profiles))


def solve_intervention_portfolio(
    items: list[AssetMaintenanceInput],
    *,
    horizon: int = 30,
    assumptions_by_asset: dict[int, InterventionAssumptions] | None = None,
    rul_scenarios: list[dict[int, float]] | None = None,
    scenario_probabilities: list[float] | None = None,
    capacity_profiles: list[CapacityProfile] | None = None,
    risk_aversion: float = 0.15,
    cvar_alpha: float = 0.90,
    allow_defer: bool = True,
    critical_rul_threshold: float = 15.0,
    max_deferrals: int | None = None,
    solver: Literal["auto", "gurobi", "highs"] = "auto",
) -> InterventionPlanResult:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if not 0 < cvar_alpha < 1:
        raise ValueError("cvar_alpha must be in (0,1)")
    if risk_aversion < 0:
        raise ValueError("risk_aversion cannot be negative")
    ids = [x.asset_id for x in items]
    if len(ids) != len(set(ids)):
        raise ValueError("asset IDs must be unique")
    assumptions = assumptions_by_asset or {x.asset_id: InterventionAssumptions() for x in items}
    missing = set(ids) - set(assumptions)
    if missing:
        raise ValueError(f"Missing intervention assumptions for assets {sorted(missing)}")
    profiles = capacity_profiles or [default_capacity_profile(horizon)]
    for p in profiles: p.validate(horizon)
    precheck = precheck_intervention_feasibility(items, profiles, horizon, assumptions_by_asset=assumptions,
                                                allow_defer=allow_defer, critical_rul_threshold=critical_rul_threshold)
    if not precheck.feasible_by_precheck:
        raise RuntimeError(f"Intervention feasibility precheck failed: {list(precheck.reasons)}")
    scenarios, probs = _normalize_scenarios(items, rul_scenarios, scenario_probabilities)
    candidates = _build_candidates(items, assumptions, horizon, allow_defer, critical_rul_threshold)
    costs = _candidate_costs(items, assumptions, candidates, scenarios)
    if solver in {"auto", "gurobi"}:
        try:
            return _solve_gurobi(items, candidates, costs, probs, profiles, horizon, cvar_alpha,
                                 risk_aversion, max_deferrals)
        except Exception as exc:
            if solver == "gurobi":
                raise
            if exc.__class__.__name__ not in {"GurobiError", "RuntimeError", "ModuleNotFoundError", "ImportError"}:
                raise
    return _solve_highs(items, candidates, costs, probs, profiles, horizon, cvar_alpha, risk_aversion, max_deferrals)


def enumerate_intervention_small_instance(
    items: list[AssetMaintenanceInput],
    *,
    horizon: int,
    assumptions_by_asset: dict[int, InterventionAssumptions] | None = None,
    rul_scenarios: list[dict[int, float]] | None = None,
    scenario_probabilities: list[float] | None = None,
    capacity_profiles: list[CapacityProfile] | None = None,
    risk_aversion: float = 0.15,
    cvar_alpha: float = 0.90,
    allow_defer: bool = True,
    critical_rul_threshold: float = 15.0,
    max_deferrals: int | None = None,
) -> tuple[float, list[InterventionChoice]]:
    assumptions = assumptions_by_asset or {x.asset_id: InterventionAssumptions() for x in items}
    profiles = capacity_profiles or [default_capacity_profile(horizon)]
    scenarios, probs = _normalize_scenarios(items, rul_scenarios, scenario_probabilities)
    candidates = _build_candidates(items, assumptions, horizon, allow_defer, critical_rul_threshold)
    costs = _candidate_costs(items, assumptions, candidates, scenarios)
    expected_candidate = probs @ costs
    groups = [[j for j, c in enumerate(candidates) if c.asset_index == i] for i in range(len(items))]
    best = float("inf"); best_selected: list[int] = []
    rows, lbs, ubs, _ = _constraint_rows(items, candidates, profiles, horizon, max_deferrals)
    for combo in product(*groups):
        x = np.zeros(len(candidates)); x[list(combo)] = 1.0
        if any((np.dot(row, x) < lo - 1e-8 if np.isfinite(lo) else False) or
               (np.dot(row, x) > hi + 1e-8 if np.isfinite(hi) else False)
               for row, lo, hi in zip(rows, lbs, ubs)):
            continue
        risk = _risk_from_selection(list(combo), costs, probs, cvar_alpha, risk_aversion)
        if risk.objective_value < best:
            best = risk.objective_value; best_selected = list(combo)
    if not best_selected:
        raise RuntimeError("No feasible intervention portfolio in enumeration oracle")
    return best, _selected_choices(items, candidates, best_selected, expected_candidate)
