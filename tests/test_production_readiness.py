from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from pdm_intelligence.api.main import app
from pdm_intelligence.security.deployment_evidence import SCHEMA_VERSION
from pdm_intelligence.security.field_validation_evidence import (
    SCHEMA_VERSION as FIELD_SCHEMA_VERSION,
)
from pdm_intelligence.security.field_validation_evidence import (
    validate_field_qualification_evidence,
)
from pdm_intelligence.security.production_readiness import production_readiness
from scripts.production_preflight import main as production_preflight_main


def test_reference_configuration_is_not_production_ready(monkeypatch):
    for name in (
        "PDM_AUTH_MODE",
        "PDM_API_KEYS",
        "PDM_SECRET_PROVIDER",
        "PDM_SECRET_ROTATION_POLICY",
        "PDM_SECRET_ROTATION_TESTED_AT",
        "PDM_STATE_BACKEND",
        "PDM_DATABASE_URL",
        "PDM_MANAGED_STATE_ADAPTER",
        "PDM_READINESS_PROBE_DATABASE",
        "PDM_DEPLOYMENT_EVIDENCE_FILE",
        "PDM_FIELD_QUALIFICATION_EVIDENCE_FILE",
        "PDM_DEPLOYMENT_ENVIRONMENT",
        "PDM_RELEASE_ARTIFACT_SHA256",
        "PDM_TLS_TERMINATED",
        "PDM_TLS_INGRESS_ID",
        "PDM_OBSERVABILITY_ENABLED",
        "PDM_METRICS_EXPORTER",
        "PDM_LOG_SINK",
        "PDM_TRACE_EXPORTER",
        "PDM_ALERT_ROUTING",
        "PDM_BACKUP_POLICY",
        "PDM_BACKUP_TARGET",
        "PDM_RESTORE_TESTED_AT",
        "PDM_CMMS_CONNECTOR_MODE",
        "PDM_CMMS_CONNECTOR_ID",
        "PDM_CMMS_CONNECTOR_ENDPOINT",
        "PDM_RELEASE_ID",
        "PDM_CHANGE_TICKET",
        "PDM_CHANGE_TICKET_STATUS",
    ):
        monkeypatch.delenv(name, raising=False)
    report = production_readiness()
    assert report["status"] == "NOT_READY"
    assert "production_auth" in report["blockers"]
    assert "managed_state_backend" in report["blockers"]


def test_production_readiness_endpoint_does_not_expose_secrets(monkeypatch):
    monkeypatch.setenv("PDM_AUTH_MODE", "production")
    monkeypatch.setenv("PDM_API_KEYS", "ops:admin:super-secret-test-key")
    response = TestClient(app).get(
        "/api/health/production-readiness",
        headers={"X-API-Key": "super-secret-test-key"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "NOT_READY"
    assert "super-secret-test-key" not in response.text
    assert payload["evidence_class"] == "CONFIGURATION_AND_ATTESTATION_NOT_DEPLOYMENT_CERTIFICATION"


def test_production_readiness_rejects_weak_or_malformed_principal_sets(monkeypatch):
    monkeypatch.setenv("PDM_AUTH_MODE", "production")
    monkeypatch.setenv("PDM_SECRET_PROVIDER", "test-secret-manager")
    monkeypatch.setenv(
        "PDM_API_KEYS",
        "ops:admin:short-key,malformed-entry,ops:admin:short-key",
    )

    report = production_readiness()
    configuration = report["checks"]["production_auth"]["configuration"]

    assert configuration["entry_count"] == 3
    assert configuration["invalid_entries"] == 1
    assert configuration["duplicate_key_entries"] == 1
    assert configuration["weak_key_entries"] == 2
    assert configuration["ready"] is False
    assert report["checks"]["production_auth"]["ready"] is False
    assert "short-key" not in str(configuration)


def test_production_readiness_requires_probe_and_attestation(tmp_path, monkeypatch):
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    controls = {
        name: {
            "status": "verified",
            "owner": "platform",
            "verified_at": now,
            "evidence_ref": f"evidence://{name}",
        }
        for name in (
            "production_auth",
            "managed_state_backend",
            "tls_termination",
            "observability",
            "backup_and_restore",
            "site_connector",
            "change_control",
        )
    }
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "release_id": "1.0.0",
        "environment": "staging",
        "artifact_sha256": "a" * 64,
        "data_fingerprint": "b" * 64,
        "attested_by": "platform",
        "attested_at": now,
        "controls": controls,
    }
    from pdm_intelligence.security.deployment_evidence import validate_deployment_evidence

    evidence["evidence_digest"] = validate_deployment_evidence(evidence)["evidence_digest"]
    path = tmp_path / "deployment-evidence.json"
    path.write_text(__import__("json").dumps(evidence), encoding="utf-8")
    for name, value in {
        "PDM_AUTH_MODE": "production",
        "PDM_API_KEYS": "ops:admin:runtime-key-012345678901234567890123456789",
        "PDM_SECRET_PROVIDER": "test-secret-manager",
        "PDM_SECRET_ROTATION_POLICY": "dual-key-30-day",
        "PDM_SECRET_ROTATION_TESTED_AT": now,
        "PDM_TLS_TERMINATED": "1",
        "PDM_TLS_INGRESS_ID": "test-ingress",
        "PDM_OBSERVABILITY_ENABLED": "1",
        "PDM_METRICS_EXPORTER": "prometheus",
        "PDM_LOG_SINK": "structured-stdout",
        "PDM_TRACE_EXPORTER": "otlp",
        "PDM_ALERT_ROUTING": "sre-oncall",
        "PDM_BACKUP_POLICY": "daily",
        "PDM_BACKUP_TARGET": "object-storage",
        "PDM_RESTORE_TESTED_AT": now,
        "PDM_CMMS_CONNECTOR_MODE": "site",
        "PDM_CMMS_CONNECTOR_ID": "site-cmms-primary",
        "PDM_CMMS_CONNECTOR_ENDPOINT": "https://cmms.example.invalid/api",
        "PDM_RELEASE_ID": "1.0.0",
        "PDM_RELEASE_ARTIFACT_SHA256": "a" * 64,
        "PDM_CHANGE_TICKET": "CHG-123",
        "PDM_CHANGE_TICKET_STATUS": "approved",
        "PDM_DEPLOYMENT_ENVIRONMENT": "staging",
        "PDM_DEPLOYMENT_EVIDENCE_FILE": str(path),
        "PDM_STATE_BACKEND": "managed",
        "PDM_MANAGED_STATE_ADAPTER": "sqlalchemy",
        "PDM_DATABASE_URL": "postgresql+psycopg://db.example/pdm",
    }.items():
        monkeypatch.setenv(name, value)
    report = production_readiness()
    assert report["deployment_evidence"]["status"] == "PASS_ATTESTATION"
    assert report["checks"]["production_auth"]["ready"] is True
    assert report["checks"]["production_auth"]["secret_rotation"] == {
        "policy": True,
        "last_test": True,
    }
    assert report["checks"]["tls_termination"]["ready"] is True
    assert report["checks"]["observability"]["ready"] is True
    assert report["checks"]["backup_and_restore"]["ready"] is True
    assert report["checks"]["site_connector"]["ready"] is True
    assert report["checks"]["managed_state_backend"]["ready"] is False
    assert report["checks"]["managed_state_backend"]["connectivity"]["status"] == "NOT_RUN"
    assert report["checks"]["managed_state_backend"]["runtime"]["status"] == "MANAGED_ADAPTER_CONFIGURED"
    assert report["checks"]["change_control"]["ready"] is True

    monkeypatch.setenv("PDM_RELEASE_ID", "2.0.0")
    mismatched = production_readiness()
    assert mismatched["deployment_evidence"]["binding"]["ready"] is False
    assert "evidence_release_id_mismatch" in mismatched["deployment_evidence"]["binding"]["errors"]
    assert mismatched["checks"]["production_auth"]["ready"] is False

    monkeypatch.setenv("PDM_RELEASE_ID", "1.0.0")
    monkeypatch.setenv("PDM_RELEASE_ARTIFACT_SHA256", "b" * 64)
    artifact_mismatched = production_readiness()
    assert artifact_mismatched["deployment_evidence"]["binding"]["ready"] is False
    assert "evidence_artifact_digest_mismatch" in artifact_mismatched["deployment_evidence"]["binding"]["errors"]


def test_production_readiness_rejects_invalid_runtime_artifact_digest(monkeypatch):
    monkeypatch.setenv("PDM_RELEASE_ARTIFACT_SHA256", "not-a-sha256")

    report = production_readiness()

    assert report["deployment_evidence"]["binding"]["ready"] is False
    assert "runtime_artifact_digest_invalid" in report["deployment_evidence"]["binding"]["errors"]


def test_field_qualification_evidence_is_release_bound_and_fail_closed(tmp_path, monkeypatch):
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    observation_start = (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {
        "schema_version": FIELD_SCHEMA_VERSION,
        "release_id": "1.0.0",
        "environment": "staging",
        "artifact_sha256": "a" * 64,
        "data_fingerprint": "b" * 64,
        "site_id": "plant-001",
        "study_id": "pilot-2026-01",
        "attested_by": "reliability-owner",
        "attested_at": now,
        "observation_start": observation_start,
        "observation_end": now,
        "qualification_status": "accepted",
        "asset_count": 12,
        "outcome_count": 4,
        "follow_up_complete": True,
        "safety_reviewed": True,
        "operator_accepted": True,
        "metrics": {
            "alert_count": 8,
            "true_event_count": 4,
            "false_alert_count": 4,
            "precision": 0.5,
            "recall": 0.75,
            "false_alert_rate": 0.5,
        },
        "data_provenance_ref": "https://evidence.example/field/data",
        "outcomes_reconciled_ref": "https://evidence.example/field/outcomes",
        "safety_review_ref": "https://evidence.example/field/safety",
        "operator_acceptance_ref": "https://evidence.example/field/acceptance",
        "results_ref": "https://evidence.example/field/results",
    }
    payload["evidence_digest"] = validate_field_qualification_evidence(payload)["evidence_digest"]
    path = tmp_path / "field-qualification.json"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    monkeypatch.setenv("PDM_FIELD_QUALIFICATION_EVIDENCE_FILE", str(path))
    monkeypatch.setenv("PDM_RELEASE_ID", "1.0.0")
    monkeypatch.setenv("PDM_DEPLOYMENT_ENVIRONMENT", "staging")
    monkeypatch.setenv("PDM_RELEASE_ARTIFACT_SHA256", "a" * 64)

    report = production_readiness()
    assert report["field_qualification_evidence"]["status"] == "PASS_ATTESTATION"
    assert report["checks"]["prospective_field_validation"]["ready"] is True

    payload["api_key"] = "must-not-be-embedded"
    payload["evidence_digest"] = validate_field_qualification_evidence(payload)["evidence_digest"]
    assert "raw_secret_material_detected" in validate_field_qualification_evidence(payload)["errors"]

    monkeypatch.setenv("PDM_RELEASE_ID", "2.0.0")
    mismatched = production_readiness()
    assert mismatched["checks"]["prospective_field_validation"]["ready"] is False
    assert "field_evidence_release_id_mismatch" in mismatched["field_qualification_evidence"]["binding"]["errors"]


def test_field_qualification_evidence_rejects_fractional_or_inconsistent_counts():
    payload = {
        "schema_version": FIELD_SCHEMA_VERSION,
        "release_id": "1.0.0",
        "environment": "staging",
        "artifact_sha256": "a" * 64,
        "data_fingerprint": "b" * 64,
        "site_id": "plant-001",
        "study_id": "pilot-2026-01",
        "attested_by": "reliability-owner",
        "attested_at": "2026-09-06T12:00:00Z",
        "observation_start": "2026-09-05T12:00:00Z",
        "observation_end": "2026-09-06T12:00:00Z",
        "qualification_status": "accepted",
        "asset_count": 12.5,
        "outcome_count": 4,
        "follow_up_complete": True,
        "safety_reviewed": True,
        "operator_accepted": True,
        "metrics": {
            "alert_count": 8,
            "true_event_count": 3,
            "false_alert_count": 4,
            "precision": 0.5,
            "recall": 0.75,
            "false_alert_rate": 0.5,
        },
        "data_provenance_ref": "https://evidence.example/field/data",
        "outcomes_reconciled_ref": "https://evidence.example/field/outcomes",
        "safety_review_ref": "https://evidence.example/field/safety",
        "operator_acceptance_ref": "https://evidence.example/field/acceptance",
        "results_ref": "https://evidence.example/field/results",
    }
    payload["evidence_digest"] = validate_field_qualification_evidence(payload)["evidence_digest"]
    errors = validate_field_qualification_evidence(payload)["errors"]
    assert "invalid_asset_count" in errors
    assert "metrics_outcome_counts_do_not_reconcile" in errors
    assert "metrics_precision_inconsistent" in errors


def test_deployment_evidence_rejects_private_key_material(monkeypatch):
    monkeypatch.setenv("PDM_DEPLOYMENT_EVIDENCE_FILE", "unused")
    from pdm_intelligence.security.deployment_evidence import validate_deployment_evidence

    private_key_marker = "-----BEGIN " + "RSA " + "PRIVATE " + "KEY-----"
    assert "raw_secret_material_detected" in validate_deployment_evidence(
        {"schema_version": SCHEMA_VERSION, "private_key": private_key_marker}
    )["errors"]


def test_production_readiness_requires_secret_rotation_evidence(monkeypatch):
    monkeypatch.setenv("PDM_AUTH_MODE", "production")
    monkeypatch.setenv("PDM_API_KEYS", "ops:admin:runtime-key-012345678901234567890123456789")
    monkeypatch.setenv("PDM_SECRET_PROVIDER", "test-secret-manager")
    monkeypatch.delenv("PDM_SECRET_ROTATION_POLICY", raising=False)
    monkeypatch.delenv("PDM_SECRET_ROTATION_TESTED_AT", raising=False)

    report = production_readiness()

    assert report["checks"]["production_auth"]["secret_rotation"] == {
        "policy": False,
        "last_test": False,
    }
    assert report["checks"]["production_auth"]["ready"] is False


def test_production_readiness_does_not_accept_partial_operational_declarations(monkeypatch):
    monkeypatch.setenv("PDM_OBSERVABILITY_ENABLED", "1")
    monkeypatch.setenv("PDM_METRICS_EXPORTER", "prometheus")
    monkeypatch.setenv("PDM_LOG_SINK", "structured-stdout")
    monkeypatch.setenv("PDM_TRACE_EXPORTER", "otlp")
    monkeypatch.delenv("PDM_ALERT_ROUTING", raising=False)
    monkeypatch.setenv("PDM_CMMS_CONNECTOR_MODE", "site")
    monkeypatch.setenv("PDM_CMMS_CONNECTOR_ID", "site-cmms-primary")
    monkeypatch.delenv("PDM_CMMS_CONNECTOR_ENDPOINT", raising=False)
    report = production_readiness()

    assert report["checks"]["observability"]["ready"] is False
    assert report["checks"]["observability"]["components_declared"]["alert_routing"] is False
    assert report["checks"]["site_connector"]["ready"] is False
    assert report["checks"]["site_connector"]["components_declared"]["connector_endpoint"] is False


def test_production_readiness_rejects_stale_restore_and_insecure_connector(monkeypatch):
    stale = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    monkeypatch.setenv("PDM_BACKUP_POLICY", "daily")
    monkeypatch.setenv("PDM_BACKUP_TARGET", "object-storage")
    monkeypatch.setenv("PDM_RESTORE_TESTED_AT", stale)
    monkeypatch.setenv("PDM_CMMS_CONNECTOR_MODE", "site")
    monkeypatch.setenv("PDM_CMMS_CONNECTOR_ID", "site-cmms-primary")
    monkeypatch.setenv("PDM_CMMS_CONNECTOR_ENDPOINT", "http://cmms.example.invalid/api")

    report = production_readiness()

    assert report["checks"]["backup_and_restore"]["ready"] is False
    assert report["checks"]["backup_and_restore"]["components_declared"]["restore_test"] is False
    assert report["checks"]["site_connector"]["ready"] is False
    assert report["checks"]["site_connector"]["components_declared"]["connector_endpoint"] is False


def test_managed_state_declaration_fails_closed_for_stateful_api(monkeypatch):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    response = TestClient(app).get("/api/operations/work-orders")
    assert response.status_code == 503
    assert response.json()["error_code"] == "MANAGED_STATE_RUNTIME_UNAVAILABLE"
    assert response.json()["reason"] == "missing_database_url"
    assert "local fallback" in response.json()["claim_boundary"]


def test_managed_stateful_api_reports_migration_required_without_implicit_ddl(monkeypatch):
    monkeypatch.setenv("PDM_STATE_BACKEND", "managed")
    monkeypatch.setenv("PDM_DATABASE_URL", "postgresql+psycopg://db.example/pdm")
    monkeypatch.setenv("PDM_MANAGED_STATE_ADAPTER", "sqlalchemy")
    monkeypatch.setattr(
        "pdm_intelligence.storage.database.managed_state_schema_check",
        lambda: {"status": "NOT_READY", "reason": "migration_missing"},
    )

    response = TestClient(app).get("/api/operations/work-orders")

    assert response.status_code == 503
    assert response.json()["error_code"] == "MANAGED_STATE_RUNTIME_UNAVAILABLE"
    assert response.json()["reason"] == "managed_state_schema_migration_required"


def test_strict_container_readiness_fails_closed_on_deployment_gate(monkeypatch):
    monkeypatch.setenv("PDM_REQUIRE_PRODUCTION_READINESS", "1")
    response = TestClient(app).get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["reason"] == "production_readiness_gate_blocked"
    assert "production_auth" in response.json()["blockers"]


def test_production_preflight_fails_closed_without_deployment_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("PDM_DEPLOYMENT_EVIDENCE_FILE", raising=False)
    monkeypatch.setattr("sys.argv", ["production_preflight.py", "--output", str(tmp_path / "preflight.json")])
    assert production_preflight_main() == 2
