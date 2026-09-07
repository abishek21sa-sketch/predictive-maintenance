from fastapi.testclient import TestClient

from pdm_intelligence.api import main


def test_reference_runtime_readiness_is_explicitly_non_production(monkeypatch):
    monkeypatch.delenv("PDM_STATE_BACKEND", raising=False)

    response = TestClient(main.app).get("/api/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["backend"] == "sqlite_reference"
    assert "does not certify production deployment" in response.json()["claim_boundary"]


def test_managed_runtime_readiness_fails_closed(monkeypatch):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setattr(
        main,
        "managed_state_connectivity",
        lambda: {"status": "NOT_READY", "reason": "test_unavailable"},
    )

    response = TestClient(main.app).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "managed_database_unavailable",
        "claim_boundary": "No local state fallback is used.",
    }


def test_managed_runtime_readiness_reports_bounded_database_probe(monkeypatch):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setattr(
        main,
        "managed_state_connectivity",
        lambda: {"status": "PASS_MANAGED_ADAPTER"},
    )
    monkeypatch.setattr(
        main,
        "managed_state_schema_check",
        lambda: {"status": "PASS_MANAGED_SCHEMA"},
    )

    response = TestClient(main.app).get("/api/health/ready")

    assert response.status_code == 200
    assert response.json()["backend"] == "managed_postgresql"
    assert response.json()["connectivity"] == "PASS_MANAGED_ADAPTER"


def test_managed_runtime_readiness_fails_closed_before_explicit_migration(monkeypatch):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setattr(
        main,
        "managed_state_connectivity",
        lambda: {"status": "PASS_MANAGED_ADAPTER"},
    )
    monkeypatch.setattr(
        main,
        "managed_state_schema_check",
        lambda: {
            "status": "NOT_READY",
            "reason": "managed_schema_or_migration_table_missing",
        },
    )

    response = TestClient(main.app).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "managed_state_schema_not_ready",
        "schema_reason": "managed_schema_or_migration_table_missing",
        "claim_boundary": "Managed runtime readiness requires an explicitly migrated and verified schema; no local state fallback is used.",
    }


def test_production_auth_mode_implies_strict_deployment_readiness(monkeypatch):
    monkeypatch.setenv("PDM_AUTH_MODE", "production")

    response = TestClient(main.app).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["reason"] == "production_readiness_gate_blocked"
    assert "production_auth" in response.json()["blockers"]
