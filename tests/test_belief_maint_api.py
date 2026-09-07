from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app

client = TestClient(app)


def test_belief_maint_reference_endpoint():
    response = client.get('/api/belief-maint/reference')
    assert response.status_code == 200
    payload = response.json()
    assert payload['gate'] == 'AUTHORIZED'
    assert payload['human_review_required'] is True
    assert len(payload['result']['schedule']) == 5


def test_belief_maint_decision_controls_are_live():
    response = client.post('/api/belief-maint/decision', json={
        'transition_stress': 1.5,
        'crew_capacity': 2,
        'opportunity_cost_scale': 1.2,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload['parameters']['transition_stress'] == 1.5
    assert payload['parameters']['crew_capacity'] == 2
    assert payload['parameters']['opportunity_cost_scale'] == 1.2


def test_belief_maint_page_is_served():
    response = client.get('/belief-maint')
    assert response.status_code == 200
    assert 'BELIEF-STATE WAR ROOM' in response.text
