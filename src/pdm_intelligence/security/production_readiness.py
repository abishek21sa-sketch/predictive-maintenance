"""Fail-closed deployment-readiness evidence for enterprise promotion.

The report checks configuration declarations only; it is not a certification of
infrastructure, security, or operational performance. A deployment pipeline can
require every check before promoting the service beyond local/reference mode.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pdm_intelligence.storage.database import managed_state_schema_check
from pdm_intelligence.storage.managed_state import (
    describe_database_url,
    managed_state_connectivity,
    managed_state_declared,
    state_runtime_status,
)

from .auth import _configured_principals, principal_configuration_report
from .deployment_evidence import is_sha256_digest, load_deployment_evidence
from .field_validation_evidence import load_field_qualification_evidence


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _configured(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


_DECLARATION_MAX_AGE = timedelta(days=30)
_CLOCK_SKEW = timedelta(minutes=5)
_SECRET_QUERY_KEYS = frozenset(
    {"access_token", "api_key", "client_secret", "password", "secret", "token"}
)


def _fresh_timestamp(value: str | None) -> bool:
    """Require a bounded, timezone-aware timestamp for operational evidence."""

    raw = (value or "").strip()
    if not raw:
        return False
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return False
    parsed_utc = parsed.astimezone(UTC)
    now = datetime.now(UTC)
    return parsed_utc <= now + _CLOCK_SKEW and now - parsed_utc <= _DECLARATION_MAX_AGE


def _secure_connector_endpoint(value: str | None) -> bool:
    """Accept only an HTTPS endpoint without embedded credentials or secret query keys."""

    raw = (value or "").strip()
    if not raw:
        return False
    try:
        parsed = urlsplit(raw)
        query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        return (
            parsed.scheme.lower() == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not query_keys.intersection(_SECRET_QUERY_KEYS)
        )
    except ValueError:
        return False


def production_readiness() -> dict[str, Any]:
    """Return non-secret deployment prerequisites and explicit blockers."""

    principals, configured = _configured_principals()
    principal_config = principal_configuration_report()
    database = describe_database_url(os.getenv("PDM_DATABASE_URL"))
    evidence = load_deployment_evidence(os.getenv("PDM_DEPLOYMENT_EVIDENCE_FILE"))
    field_evidence = load_field_qualification_evidence(
        os.getenv("PDM_FIELD_QUALIFICATION_EVIDENCE_FILE")
    )
    verified_controls = set(evidence.get("verified_controls", []))
    evidence_ready = evidence.get("status") == "PASS_ATTESTATION"
    runtime_release_id = os.getenv("PDM_RELEASE_ID", "").strip()
    runtime_environment = os.getenv("PDM_DEPLOYMENT_ENVIRONMENT", "").strip()
    runtime_artifact_sha256 = os.getenv("PDM_RELEASE_ARTIFACT_SHA256", "").strip()
    evidence_binding_errors: list[str] = []
    if not runtime_release_id:
        evidence_binding_errors.append("runtime_release_id_not_configured")
    elif evidence.get("release_id") != runtime_release_id:
        evidence_binding_errors.append("evidence_release_id_mismatch")
    if not runtime_environment:
        evidence_binding_errors.append("runtime_environment_not_configured")
    elif evidence.get("environment") != runtime_environment:
        evidence_binding_errors.append("evidence_environment_mismatch")
    if not runtime_artifact_sha256:
        evidence_binding_errors.append("runtime_artifact_digest_not_configured")
    elif not is_sha256_digest(runtime_artifact_sha256):
        evidence_binding_errors.append("runtime_artifact_digest_invalid")
    elif evidence.get("artifact_sha256") != runtime_artifact_sha256:
        evidence_binding_errors.append("evidence_artifact_digest_mismatch")
    evidence_bound = evidence_ready and not evidence_binding_errors

    field_binding_errors: list[str] = []
    if not runtime_release_id:
        field_binding_errors.append("runtime_release_id_not_configured")
    elif field_evidence.get("release_id") != runtime_release_id:
        field_binding_errors.append("field_evidence_release_id_mismatch")
    if not runtime_environment:
        field_binding_errors.append("runtime_environment_not_configured")
    elif field_evidence.get("environment") != runtime_environment:
        field_binding_errors.append("field_evidence_environment_mismatch")
    if not runtime_artifact_sha256:
        field_binding_errors.append("runtime_artifact_digest_not_configured")
    elif not is_sha256_digest(runtime_artifact_sha256):
        field_binding_errors.append("runtime_artifact_digest_invalid")
    elif field_evidence.get("artifact_sha256") != runtime_artifact_sha256:
        field_binding_errors.append("field_evidence_artifact_digest_mismatch")
    field_evidence_bound = field_evidence.get("status") == "PASS_ATTESTATION" and not field_binding_errors

    def attested(control: str) -> bool:
        return evidence_bound and control in verified_controls

    managed_declared = managed_state_declared()
    state_runtime = state_runtime_status()
    if managed_declared and _truthy("PDM_READINESS_PROBE_DATABASE"):
        managed_connectivity = managed_state_connectivity()
    else:
        managed_connectivity = {
            "status": "NOT_RUN",
            "reason": "set PDM_READINESS_PROBE_DATABASE=1 to require a bounded live connectivity check",
        }
    if managed_connectivity.get("status") == "PASS_MANAGED_ADAPTER":
        managed_schema = managed_state_schema_check()
    else:
        managed_schema = {
            "status": "NOT_RUN",
            "reason": "managed schema check requires a passing live connectivity probe",
            "stores": [],
        }
    production_auth_declared = (
        os.getenv("PDM_AUTH_MODE", "local").strip().lower() == "production"
        and configured
        and bool(principals)
        and principal_config["ready"] is True
        and _configured("PDM_SECRET_PROVIDER")
        and _configured("PDM_SECRET_ROTATION_POLICY")
        and _fresh_timestamp(os.getenv("PDM_SECRET_ROTATION_TESTED_AT"))
    )
    secret_rotation = {
        "policy": _configured("PDM_SECRET_ROTATION_POLICY"),
        "last_test": _fresh_timestamp(os.getenv("PDM_SECRET_ROTATION_TESTED_AT")),
    }
    observability_components = {
        "metrics_exporter": _configured("PDM_METRICS_EXPORTER"),
        "log_sink": _configured("PDM_LOG_SINK"),
        "trace_exporter": _configured("PDM_TRACE_EXPORTER"),
        "alert_routing": _configured("PDM_ALERT_ROUTING"),
    }
    observability_declared = _truthy("PDM_OBSERVABILITY_ENABLED") and all(
        observability_components.values()
    )
    backup_components = {
        "policy": _configured("PDM_BACKUP_POLICY"),
        "target": _configured("PDM_BACKUP_TARGET"),
        "restore_test": _fresh_timestamp(os.getenv("PDM_RESTORE_TESTED_AT")),
    }
    backup_declared = all(backup_components.values())
    site_connector_declared = (
        os.getenv("PDM_CMMS_CONNECTOR_MODE", "reference").strip().lower()
        not in {"", "reference", "mock", "local"}
        and _configured("PDM_CMMS_CONNECTOR_ID")
        and _secure_connector_endpoint(os.getenv("PDM_CMMS_CONNECTOR_ENDPOINT"))
    )
    checks = {
        "production_auth": {
            "ready": production_auth_declared and attested("production_auth"),
            "requirement": (
                "PDM_AUTH_MODE=production, valid PDM_API_KEYS from a declared secret provider, "
                "a current secret-rotation policy/test, and deployment evidence attestation"
            ),
            "configuration": principal_config,
            "secret_rotation": secret_rotation,
        },
        "managed_state_backend": {
            "ready": (
                managed_declared
                and managed_connectivity.get("status") == "PASS_MANAGED_ADAPTER"
                and managed_schema.get("status") == "PASS_MANAGED_SCHEMA"
                and state_runtime.get("ready") is True
                and attested("managed_state_backend")
            ),
            "requirement": (
                "managed multi-user SQLAlchemy adapter, supported PDM_DATABASE_URL, "
                "PDM_MANAGED_STATE_ADAPTER=sqlalchemy, live connectivity probe, and deployment evidence"
            ),
            "configured_scheme": database.scheme or None,
            "configured_url": database.redacted_url or None,
            "configuration_valid": database.valid,
            "connectivity": {
                "status": managed_connectivity.get("status"),
                "reason": managed_connectivity.get("reason"),
            },
            "schema": managed_schema,
            "runtime": state_runtime,
        },
        "tls_termination": {
            "ready": (
                _truthy("PDM_TLS_TERMINATED")
                and _configured("PDM_TLS_INGRESS_ID")
                and attested("tls_termination")
            ),
            "requirement": (
                "TLS termination is enabled at the trusted ingress boundary, identified by "
                "PDM_TLS_INGRESS_ID, and attested"
            ),
        },
        "observability": {
            "ready": observability_declared and attested("observability"),
            "requirement": (
                "PDM_OBSERVABILITY_ENABLED plus metrics exporter, log sink, trace exporter, "
                "alert routing, and deployment evidence"
            ),
            "components_declared": observability_components,
        },
        "backup_and_restore": {
            "ready": backup_declared and attested("backup_and_restore"),
            "requirement": (
                "backup policy, backup target, current timezone-aware restore test, and deployment evidence are present"
            ),
            "components_declared": backup_components,
        },
        "site_connector": {
            "ready": site_connector_declared and attested("site_connector"),
            "requirement": (
                "a site-specific CMMS/ERP connector ID and endpoint are configured and attested"
            ),
            "components_declared": {
                "connector_id": _configured("PDM_CMMS_CONNECTOR_ID"),
                "connector_endpoint": _secure_connector_endpoint(
                    os.getenv("PDM_CMMS_CONNECTOR_ENDPOINT")
                ),
            },
        },
        "change_control": {
            "ready": bool(os.getenv("PDM_RELEASE_ID", "").strip())
            and bool(os.getenv("PDM_CHANGE_TICKET", "").strip())
            and os.getenv("PDM_CHANGE_TICKET_STATUS", "").strip().lower() == "approved"
            and attested("change_control"),
            "requirement": "release ID, deployed artifact SHA-256, approved change ticket status, and deployment evidence are recorded",
        },
        "prospective_field_validation": {
            "ready": field_evidence_bound,
            "requirement": (
                "release-bound prospective field evidence with completed follow-up, reconciled outcomes, "
                "safety review, operator acceptance, and a non-secret evidence attestation"
            ),
            "evidence": {
                "status": field_evidence.get("status"),
                "site_id": field_evidence.get("site_id"),
                "study_id": field_evidence.get("study_id"),
                "qualification_status": field_evidence.get("qualification_status"),
                "errors": field_evidence.get("errors", []),
            },
        },
    }
    blockers = [name for name, item in checks.items() if not item["ready"]]
    return {
        "status": "READY_FOR_DEPLOYMENT_REVIEW" if not blockers else "NOT_READY",
        "evidence_class": "CONFIGURATION_AND_ATTESTATION_NOT_DEPLOYMENT_CERTIFICATION",
        "deployment_evidence": {
            "status": evidence.get("status"),
            "verified_controls": evidence.get("verified_controls", []),
            "errors": evidence.get("errors", []),
            "evidence_digest": evidence.get("evidence_digest"),
            "binding": {
                "ready": evidence_bound,
                "errors": evidence_binding_errors,
                "runtime_release_id": runtime_release_id or None,
                "runtime_environment": runtime_environment or None,
                "runtime_artifact_sha256": runtime_artifact_sha256 or None,
                "evidence_release_id": evidence.get("release_id"),
                "evidence_environment": evidence.get("environment"),
                "evidence_artifact_sha256": evidence.get("artifact_sha256"),
            },
        },
        "field_qualification_evidence": {
            "status": field_evidence.get("status"),
            "evidence_class": field_evidence.get("evidence_class"),
            "errors": field_evidence.get("errors", []),
            "evidence_digest": field_evidence.get("evidence_digest"),
            "binding": {
                "ready": field_evidence_bound,
                "errors": field_binding_errors,
                "runtime_release_id": runtime_release_id or None,
                "runtime_environment": runtime_environment or None,
                "runtime_artifact_sha256": runtime_artifact_sha256 or None,
                "evidence_release_id": field_evidence.get("release_id"),
                "evidence_environment": field_evidence.get("environment"),
                "evidence_artifact_sha256": field_evidence.get("artifact_sha256"),
            },
        },
        "checks": checks,
        "blockers": blockers,
        "claim_boundary": (
            "This report checks non-secret configuration declarations, bounded managed-state connectivity, "
            "structured deployment and field-qualification attestations. It does not certify infrastructure, "
            "cybersecurity, compliance, availability, model performance, or field safety."
        ),
    }
