import math

import numpy as np

from pdm_intelligence.models.rul import residual_interval
from pdm_intelligence.optimization.maintenance import (
    AssetMaintenanceInput,
    enumerate_small_instance,
    solve_maintenance,
    validate_schedule,
)
from pdm_intelligence.reliability.analysis import fit_reliability, weibull_hazard
from pdm_intelligence.reliability.economics import expected_maintenance_economics


def test_multicycle_jobs_really_occupy_shop_capacity():
    items = [
        AssetMaintenanceInput(1, 8, 0.8, duration=2, labor_hours=8),
        AssetMaintenanceInput(2, 9, 0.75, duration=2, labor_hours=8),
    ]
    result = solve_maintenance(items, horizon=4, capacity_per_cycle=1, solver="highs")
    check = validate_schedule(result.schedule, items, horizon=4, capacity_per_cycle=1)
    assert check["feasible"], check
    a, b = sorted(result.schedule, key=lambda x: x.cycle)
    assert a.cycle + a.duration <= b.cycle


def test_milp_matches_exact_enumeration_oracle():
    items = [
        AssetMaintenanceInput(1, 5, 0.8, duration=1),
        AssetMaintenanceInput(2, 7, 0.6, duration=1),
        AssetMaintenanceInput(3, 10, 0.4, duration=1),
    ]
    exact_obj, _ = enumerate_small_instance(items, horizon=3, capacity_per_cycle=1)
    result = solve_maintenance(items, horizon=3, capacity_per_cycle=1, solver="highs")
    assert result.evidence.status == "OPTIMAL"
    assert math.isclose(result.objective_value, exact_obj, rel_tol=1e-9, abs_tol=1e-6)


def test_availability_and_weibull_hazard_boundary_math():
    rel = fit_reliability([100, 120, 140, 160], repair_durations=[10, 10, 10, 10])
    assert math.isclose(rel.availability, rel.mtbf_cycles / (rel.mtbf_cycles + rel.mttr_cycles))
    hz = weibull_hazard([rel.weibull_scale], rel.weibull_shape, rel.weibull_scale)[0]
    assert math.isclose(hz, rel.weibull_shape / rel.weibull_scale, rel_tol=1e-9)


def test_maintenance_economics_closes_expected_cost_equation():
    p = 0.25
    econ = expected_maintenance_economics(p, preventive_cost=5000, corrective_cost=30000,
                                          corrective_downtime_cycles=6, preventive_downtime_cycles=1,
                                          downtime_cost_per_cycle=1000)
    expected = 5000 + p * 30000 + (p * 6 + (1-p) * 1) * 1000
    assert math.isclose(econ.expected_total_cost, expected)
    assert math.isclose(econ.expected_avoided_cost_vs_run_to_failure, 36000 - expected)


def test_residual_interval_is_nonnegative_and_symmetric_above_zero():
    lo, hi = residual_interval(np.array([5.0, 20.0]), 7.0)
    assert np.allclose(lo, [0.0, 13.0])
    assert np.allclose(hi, [12.0, 27.0])
