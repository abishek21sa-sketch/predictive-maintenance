from pdm_intelligence.fourx.belief_maint_decision import build_belief_maint_decision


def test_belief_maint_governed_decision_is_authorized_and_review_gated():
    decision = build_belief_maint_decision()
    assert decision["gate"] == "AUTHORIZED"
    assert decision["human_review_required"] is True
    assert all(decision["checks"].values())
    assert decision["decision_id"].startswith("BELIEF-")


def test_decision_id_is_deterministic():
    assert build_belief_maint_decision()["decision_id"] == build_belief_maint_decision()["decision_id"]


def test_reference_exposes_all_baselines_and_belief_lineage():
    decision = build_belief_maint_decision()
    assert set(decision["baselines"]) == {"fixed_interval", "risk_rank", "lowest_production_load"}
    assert len(decision["belief_summary"]) == 5
    assert len(decision["actions"]) == 5


def test_stress_control_changes_modeled_failure_path():
    base = build_belief_maint_decision(transition_stress=1.0)
    stress = build_belief_maint_decision(transition_stress=1.5)
    b = next(x for x in base["belief_summary"] if x["asset_id"] == 103)
    s = next(x for x in stress["belief_summary"] if x["asset_id"] == 103)
    assert s["horizon_end"]["failed"] > b["horizon_end"]["failed"]
