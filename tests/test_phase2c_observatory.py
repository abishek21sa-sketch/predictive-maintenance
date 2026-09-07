from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app


def test_observatory_is_not_dashboard_shell_and_has_radial_identity():
    c = TestClient(app)
    html = c.get('/').text
    assert 'Reliability Observatory' in html
    assert 'FLEET ORBIT' in html
    assert 'DECISION FIELD' in html
    assert 'SCHEDULE CLOCK' in html
    assert 'METHODS ↗' in html
    for rejected in ('ASSET × TIME', 'Fleet commitments', 'Asset Intervention Desk', 'Detect', 'KPI'):
        assert rejected not in html


def test_methodology_page_explains_actual_ai_ie_or_simulation_chain():
    c = TestClient(app)
    r = c.get('/methodology')
    assert r.status_code == 200
    html = r.text
    for expected in (
        'ARTIFICIAL INTELLIGENCE',
        'RELIABILITY ENGINEERING',
        'OPERATIONS RESEARCH',
        'SIMULATION',
        'Gurobi chooses a feasible intervention plan',
        'R(t)=',
        'MTBF',
        'MIP gap',
        'NOT CLAIMED',
    ):
        assert expected in html


def test_observatory_keeps_case_decision_and_commitment_endpoints_live():
    c = TestClient(app)
    cases = c.get('/api/casebook?limit=5').json()
    assert len(cases) == 5
    aid = cases[0]['asset_id']
    assert c.get(f'/api/assets/{aid}/dossier').status_code == 200
    options = c.get(f'/api/assets/{aid}/intervention-options').json()
    assert {x['id'] for x in options['options']} == {'maintain_now', 'inspect_first', 'defer_5'}
    infeasible = c.get('/api/commitment-ledger?scenario=accelerated_degradation&bay_capacity=1')
    assert infeasible.status_code == 422
    plan = c.get('/api/commitment-ledger?scenario=accelerated_degradation&bay_capacity=2')
    assert plan.status_code == 200
    assert plan.json()['feasibility']['feasible'] is True


def test_observatory_frontend_is_self_contained_and_cache_safe():
    c = TestClient(app)
    home = c.get('/')
    assert home.status_code == 200
    assert home.headers.get('cache-control') == 'no-store, max-age=0'
    html = home.text
    assert 'data-bundled="observatory-css"' in html
    assert 'data-bundled="observatory-runtime"' in html
    assert '/static/styles.css' not in html
    assert '/static/app.js' not in html
    # Guard against the exact stale Phase-2B runtime observed on Windows.
    assert 'CASEBOOK ERROR' not in html
    assert 'OBSERVATORY ERROR' in html

    methods = c.get('/methodology')
    assert methods.status_code == 200
    assert methods.headers.get('cache-control') == 'no-store, max-age=0'
    assert 'data-bundled="observatory-css"' in methods.text
    assert 'data-bundled="methodology-runtime"' in methods.text
    assert '/static/styles.css' not in methods.text
    assert '/static/methodology.js' not in methods.text
