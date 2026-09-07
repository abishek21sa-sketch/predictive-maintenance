from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app


def test_api_authentication_boundary_protects_routes_without_local_dependencies(monkeypatch):
    monkeypatch.setenv("PDM_AUTH_MODE", "production")
    monkeypatch.setenv("PDM_API_KEYS", "ops:admin:runtime-key")
    monkeypatch.setenv("PDM_SECRET_PROVIDER", "test-secret-manager")

    client = TestClient(app)

    # Orchestration liveness remains intentionally non-sensitive and public.
    assert client.get("/api/health").status_code == 200
    ready = client.get("/api/health/ready")
    assert ready.status_code == 503
    assert ready.json()["reason"] == "production_readiness_gate_blocked"

    # Every other API route is authenticated even if a route omitted a local
    # dependency, preventing accidental anonymous read surfaces.
    assert client.get("/api/portfolio").status_code == 401
    assert client.get("/api/health/production-readiness").status_code == 401
    authorized = client.get("/api/portfolio", headers={"X-API-Key": "runtime-key"})
    assert authorized.status_code == 200


def test_viewer_cannot_invoke_mutating_or_compute_permissions(monkeypatch):
    monkeypatch.setenv("PDM_AUTH_MODE", "production")
    monkeypatch.setenv(
        "PDM_API_KEYS",
        "viewer:viewer-key,planner:planner-key,data:data-key,scientist:data-scientist-key",
    )
    monkeypatch.setenv("PDM_SECRET_PROVIDER", "test-secret-manager")
    client = TestClient(app)
    headers = {"X-API-Key": "viewer-key"}

    assert client.post("/api/predict", json={"records": []}, headers=headers).status_code == 403
    assert client.post("/api/optimize", json={"assets": []}, headers=headers).status_code == 403
    assert client.post("/api/reliability/kpis", json={"events": [], "operating_cycles": 1}, headers=headers).status_code == 403
    assert client.post(
        "/api/data/preview/text",
        json={"name": "preview", "files": []},
        headers=headers,
    ).status_code == 403
    assert client.post(
        "/api/data/ingest/text",
        json={"name": "ingest", "files": []},
        headers=headers,
    ).status_code == 403
    assert client.post(
        "/api/models/external/rul/train",
        json={"dataset_id": "dataset-1"},
        headers=headers,
    ).status_code == 403
    assert client.post(
        "/api/monitoring/drift",
        json={"reference": [], "current": []},
        headers=headers,
    ).status_code == 403
    assert client.post(
        "/api/operations/metropt/work-orders/commit",
        json={"scenario": "baseline", "bays": 2},
        headers=headers,
    ).status_code == 403
    assert client.patch(
        "/api/operations/work-orders/WO-1/status",
        json={"status": "IN_PROGRESS"},
        headers=headers,
    ).status_code == 403
