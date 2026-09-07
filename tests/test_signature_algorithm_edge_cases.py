import numpy as np
import pytest

from pdm_intelligence.fourx.signature_algorithm import (
    BeliefMaintAsset,
    BeliefMaintError,
    baseline_schedules,
    evaluate_schedule_objective,
    make_reference_problem,
    solve_belief_maint,
    stress_transition_matrix,
    validate_assets,
    validate_transition_matrix,
)


def test_result_to_dict_and_integer_capacity_path():
    assets,p,h,_=make_reference_problem(); r=solve_belief_maint(assets,p,horizon=h,crew_capacity_by_cycle=1)
    d=r.to_dict(); assert d['algorithm']=='BELIEF-MAINT' and len(d['schedule'])==len(assets)

def test_transition_validation_rejects_bad_shapes_negative_and_nonstochastic():
    with pytest.raises(BeliefMaintError): validate_transition_matrix(np.eye(3))
    p=np.eye(4); p[0,1]=-0.1; p[0,0]=1.1
    with pytest.raises(BeliefMaintError): validate_transition_matrix(p)
    p=np.eye(4); p[0,0]=.9
    with pytest.raises(BeliefMaintError): validate_transition_matrix(p)

def test_asset_validation_rejects_bad_inputs():
    a=BeliefMaintAsset(1,(1,0,0,0),1,1,(1,))
    with pytest.raises(BeliefMaintError): validate_assets([a],0)
    with pytest.raises(BeliefMaintError): validate_assets([],1)
    with pytest.raises(BeliefMaintError): validate_assets([a,a],1)
    with pytest.raises(BeliefMaintError): validate_assets([BeliefMaintAsset(2,(.5,.5,0,.1),1,1,(1,))],1)
    with pytest.raises(BeliefMaintError): validate_assets([BeliefMaintAsset(2,(1,0,0,0),1,1,(1,2))],1)
    with pytest.raises(BeliefMaintError): validate_assets([BeliefMaintAsset(2,(1,0,0,0),-1,1,(1,))],1)

def test_capacity_and_baseline_validation_edges():
    assets,p,h,_=make_reference_problem()
    with pytest.raises(BeliefMaintError): solve_belief_maint(assets,p,horizon=h,crew_capacity_by_cycle=[1,1])
    with pytest.raises(BeliefMaintError): baseline_schedules(assets,p,horizon=h,crew_capacity_by_cycle=[1,0,1,1,1,1])
    with pytest.raises(BeliefMaintError): baseline_schedules(assets,p,horizon=h,crew_capacity_by_cycle=[1,1,1,1,0,1])
    with pytest.raises(BeliefMaintError): evaluate_schedule_objective(assets,p,horizon=h,maintenance_cycle_by_asset={a.asset_id:h+1 for a in assets})

def test_transition_stress_validation_and_extreme_rescaling():
    _,p,_,_=make_reference_problem()
    with pytest.raises(BeliefMaintError): stress_transition_matrix(p,0)
    stressed=stress_transition_matrix(p,100)
    assert np.allclose(stressed.sum(axis=1),1.0)
    assert stressed[2,3] < 1.0 and stressed[2,3] > .9
