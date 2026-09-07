import numpy as np
import pytest

from pdm_intelligence.fourx.signature_algorithm import (
    BeliefMaintError,
    baseline_schedules,
    make_reference_problem,
    propagate_beliefs,
    solve_belief_maint,
    stress_transition_matrix,
    validate_transition_matrix,
)


def test_transition_matrix_and_beliefs_are_valid_probabilities():
    assets, p, horizon, _ = make_reference_problem()
    validate_transition_matrix(p)
    histories = propagate_beliefs(assets, p, horizon)
    for hist in histories.values():
        assert np.allclose(hist.sum(axis=1), 1.0)
        assert np.all(hist >= -1e-12)
        assert np.all(np.diff(hist[:, 3]) >= -1e-12)


def test_failed_state_must_be_absorbing():
    _, p, _, _ = make_reference_problem()
    bad = p.copy(); bad[3] = [0.1, 0, 0, 0.9]
    with pytest.raises(BeliefMaintError):
        validate_transition_matrix(bad)


def test_belief_maint_is_feasible_and_respects_crew_capacity():
    assets, p, horizon, capacity = make_reference_problem()
    result = solve_belief_maint(assets, p, horizon=horizon, crew_capacity_by_cycle=capacity)
    assert result.status == "OPTIMAL"
    assert len(result.schedule) == len(assets)
    cycles = [row.maintenance_cycle for row in result.schedule]
    assert len(cycles) == len(set(cycles))  # capacity=1 in reference problem
    assert all(1 <= cycle <= horizon for cycle in cycles)


def test_belief_maint_not_worse_than_governed_heuristic_baselines():
    assets, p, horizon, capacity = make_reference_problem()
    result = solve_belief_maint(assets, p, horizon=horizon, crew_capacity_by_cycle=capacity)
    baselines = baseline_schedules(assets, p, horizon=horizon, crew_capacity_by_cycle=capacity)
    assert all(result.objective_value <= b["objective_value"] + 1e-6 for b in baselines.values())


def test_stress_increases_failure_belief_and_can_change_timing():
    assets, p, horizon, capacity = make_reference_problem()
    base = solve_belief_maint(assets, p, horizon=horizon, crew_capacity_by_cycle=capacity)
    stressed_p = stress_transition_matrix(p, 1.5)
    stressed_hist = propagate_beliefs(assets, stressed_p, horizon)
    base_hist = propagate_beliefs(assets, p, horizon)
    assert stressed_hist[103][-1, 3] > base_hist[103][-1, 3]
    stressed = solve_belief_maint(assets, stressed_p, horizon=horizon, crew_capacity_by_cycle=capacity)
    assert stressed.status == "OPTIMAL"
    assert stressed.objective_value > base.objective_value
