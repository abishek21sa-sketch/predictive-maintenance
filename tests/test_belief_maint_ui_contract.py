from pathlib import Path

HTML = Path('src/pdm_intelligence/frontend/belief_maint.html').read_text(encoding='utf-8')
INDEX = Path('src/pdm_intelligence/frontend/index.html').read_text(encoding='utf-8')


def test_belief_maint_surface_has_required_operator_elements():
    for token in [
        'DEGRADATION STRESS', 'CREW CAPACITY / CYCLE', 'PRODUCTION LOSS SCALE',
        'Degradation transition matrix', 'Counterfactual baselines',
        'Crew-constrained maintenance sequence', 'Belief trajectories', 'Evidence boundary'
    ]:
        assert token in HTML


def test_belief_maint_surface_calls_live_decision_api():
    assert "/api/belief-maint/decision" in HTML
    assert 'href="/belief-maint"' in INDEX
