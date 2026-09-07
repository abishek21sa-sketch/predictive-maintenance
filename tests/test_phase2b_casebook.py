from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app, release_snapshot
from pdm_intelligence.planner.casebook import asset_dossier, fleet_casebook, intervention_options


def test_casebook_ranks_and_labels_cases():
    rows=fleet_casebook(release_snapshot()['assets'], limit=12)
    assert len(rows)==12
    assert rows[0]['asset_label'].startswith('ENG-')
    assert rows[0]['risk_band'] in {'critical','high','watch','stable'}


def test_dossier_is_honestly_labeled_and_bounded():
    aid=fleet_casebook(release_snapshot()['assets'],1)[0]['asset_id']
    d=asset_dossier(release_snapshot()['assets'],aid)
    assert d['diagnostic_trace_class']=='DERIVED_DIAGNOSTIC_VISUALIZATION_NOT_OBSERVED_SENSOR_HISTORY'
    assert d['survival_trace_class']=='MODELED_PROGNOSTIC_VISUALIZATION'
    assert all(0 <= p['health_index'] <= 1 for p in d['diagnostic_trace'])
    assert all(0 <= p['survival_proxy'] <= 1 for p in d['survival_trace'])


def test_intervention_lab_has_three_distinct_modeled_options():
    aid=fleet_casebook(release_snapshot()['assets'],1)[0]['asset_id']
    body=intervention_options(release_snapshot()['assets'],aid)
    assert body['evidence_class']=='MODELED_INTERVENTION_ALTERNATIVES'
    assert {x['id'] for x in body['options']}=={'maintain_now','inspect_first','defer_5'}
    assert all(x['economics']['expected_total_cost'] >= 0 for x in body['options'])


def test_casebook_api_and_new_identity_contract():
    c=TestClient(app)
    assert c.get('/api/casebook').status_code==200
    aid=c.get('/api/casebook?limit=1').json()[0]['asset_id']
    assert c.get(f'/api/assets/{aid}/dossier').status_code==200
    assert c.get(f'/api/assets/{aid}/intervention-options').status_code==200
    ledger=c.get('/api/commitment-ledger?bay_capacity=2').json()
    assert ledger['feasibility']['feasible'] is True
    html=c.get('/').text
    assert 'Reliability Observatory' in html
    assert 'FLEET ORBIT' in html
    assert 'SCHEDULE CLOCK' in html
    assert 'Detect' not in html
    assert 'ASSET × TIME' not in html
