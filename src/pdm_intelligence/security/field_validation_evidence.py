"""Validate non-secret evidence for prospective field qualification.

Historical benchmark and public-dataset results cannot certify that a release
works on a customer's equipment.  This module adds a separate, release-bound
attestation for prospective field evaluation.  It validates the shape and
freshness of the evidence; it does not independently certify safety,
performance, or regulatory compliance.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .deployment_evidence import is_sha256_digest

SCHEMA_VERSION = "PDM-FIELD-QUALIFICATION.v1"
_MAX_EVIDENCE_AGE = timedelta(days=180)
_CLOCK_SKEW = timedelta(minutes=5)
_SECRET_QUERY_KEYS = frozenset(
    {"access_token", "api_key", "client_secret", "password", "secret", "token"}
)
_RAW_SECRET_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "client_secret",
        "password",
        "private_key",
        "raw_secret",
        "secret_value",
    }
)


def _contains_raw_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key.lower() in _RAW_SECRET_KEYS or _contains_raw_secret(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_raw_secret(item) for item in value)
    if isinstance(value, str):
        return "PRIVATE KEY-----" in value.upper()
    return False


def _evidence_ref_error(value: Any, field: str) -> str | None:
    """Require a non-secret URI for an operator-held qualification record."""

    if not isinstance(value, str) or not value.strip():
        return f"missing_{field}"
    reference = value.strip()
    parsed = urlsplit(reference)
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.scheme.lower() in {"data", "file"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        return f"invalid_{field}"
    try:
        query_keys = {key.lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    except ValueError:
        return f"invalid_{field}"
    if query_keys & _SECRET_QUERY_KEYS:
        return f"secret_bearing_{field}"
    return None


def _timestamp_error(value: Any, field: str, now: datetime) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return f"missing_{field}"
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return f"invalid_{field}_timestamp"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return f"{field}_timestamp_requires_timezone"
    parsed_utc = parsed.astimezone(UTC)
    if parsed_utc > now + _CLOCK_SKEW:
        return f"{field}_timestamp_in_future"
    if now - parsed_utc > _MAX_EVIDENCE_AGE:
        return f"{field}_timestamp_expired"
    return None


def _number_error(value: Any, field: str, *, minimum: float = 0.0, maximum: float | None = None) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return f"invalid_{field}"
    if float(value) < minimum or (maximum is not None and float(value) > maximum):
        return f"invalid_{field}_range"
    return None


def _integer_error(value: Any, field: str, *, minimum: int = 0, maximum: int | None = None) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return f"invalid_{field}"
    if value < minimum or (maximum is not None and value > maximum):
        return f"invalid_{field}_range"
    return None


def _canonical_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(canonical).hexdigest()


def validate_field_qualification_evidence(payload: Any) -> dict[str, Any]:
    """Return a non-secret validation report for prospective field evidence."""

    errors: list[str] = []
    now = datetime.now(UTC)
    if not isinstance(payload, dict):
        return {
            "status": "INVALID",
            "evidence_class": "PROSPECTIVE_FIELD_QUALIFICATION_ATTESTATION_NOT_CERTIFICATION",
            "errors": ["root_must_be_object"],
        }
    if _contains_raw_secret(payload):
        errors.append("raw_secret_material_detected")
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported_schema_version")
    for field in (
        "release_id",
        "environment",
        "artifact_sha256",
        "data_fingerprint",
        "site_id",
        "study_id",
        "attested_by",
        "qualification_status",
    ):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            errors.append(f"missing_{field}")
    if payload.get("artifact_sha256") and not is_sha256_digest(payload["artifact_sha256"]):
        errors.append("invalid_artifact_sha256")
    if payload.get("data_fingerprint") and not is_sha256_digest(payload["data_fingerprint"]):
        errors.append("invalid_data_fingerprint")
    if payload.get("qualification_status") != "accepted":
        errors.append("qualification_not_accepted")
    for field in ("attested_at", "observation_start", "observation_end"):
        error = _timestamp_error(payload.get(field), field, now)
        if error:
            errors.append(error)
    try:
        start = datetime.fromisoformat(payload["observation_start"].strip())
        end = datetime.fromisoformat(payload["observation_end"].strip())
        if start.tzinfo is not None and end.tzinfo is not None and end <= start:
            errors.append("observation_period_not_increasing")
    except (KeyError, AttributeError, TypeError, ValueError):
        pass
    for field in ("asset_count", "outcome_count"):
        error = _integer_error(payload.get(field), field, minimum=1)
        if error:
            errors.append(error)
    if payload.get("follow_up_complete") is not True:
        errors.append("follow_up_not_complete")
    if payload.get("safety_reviewed") is not True:
        errors.append("safety_review_not_confirmed")
    if payload.get("operator_accepted") is not True:
        errors.append("operator_acceptance_not_confirmed")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics_must_be_object")
        metrics = {}
    for field in ("alert_count", "true_event_count", "false_alert_count"):
        error = _integer_error(
            metrics.get(field),
            f"metrics_{field}",
            minimum=1 if field == "alert_count" else 0,
        )
        if error:
            errors.append(error)
    if all(
        isinstance(metrics.get(field), int) and not isinstance(metrics.get(field), bool)
        for field in ("alert_count", "true_event_count", "false_alert_count")
    ):
        reconciled_count = metrics["true_event_count"] + metrics["false_alert_count"]
        if reconciled_count > metrics["alert_count"]:
            errors.append("metrics_outcome_counts_exceed_alert_count")
        elif reconciled_count != metrics["alert_count"]:
            errors.append("metrics_outcome_counts_do_not_reconcile")
    for field in ("precision", "recall", "false_alert_rate"):
        error = _number_error(metrics.get(field), f"metrics_{field}", maximum=1.0)
        if error:
            errors.append(error)
    if (
        isinstance(metrics.get("alert_count"), int)
        and metrics["alert_count"] > 0
        and isinstance(metrics.get("true_event_count"), int)
        and isinstance(metrics.get("false_alert_count"), int)
        and isinstance(metrics.get("precision"), (int, float))
        and isinstance(metrics.get("false_alert_rate"), (int, float))
    ):
        expected_precision = metrics["true_event_count"] / metrics["alert_count"]
        expected_false_alert_rate = metrics["false_alert_count"] / metrics["alert_count"]
        if not math.isclose(float(metrics["precision"]), expected_precision, rel_tol=0.0, abs_tol=1e-6):
            errors.append("metrics_precision_inconsistent")
        if not math.isclose(float(metrics["false_alert_rate"]), expected_false_alert_rate, rel_tol=0.0, abs_tol=1e-6):
            errors.append("metrics_false_alert_rate_inconsistent")
    for field in (
        "data_provenance_ref",
        "outcomes_reconciled_ref",
        "safety_review_ref",
        "operator_acceptance_ref",
        "results_ref",
    ):
        error = _evidence_ref_error(payload.get(field), field)
        if error:
            errors.append(error)
    digest_input = {key: value for key, value in payload.items() if key != "evidence_digest"}
    calculated = _canonical_digest(digest_input)
    supplied = payload.get("evidence_digest")
    if not isinstance(supplied, str) or not supplied.strip():
        errors.append("missing_evidence_digest")
    elif supplied != calculated:
        errors.append("evidence_digest_mismatch")
    return {
        "status": "PASS_ATTESTATION" if not errors else "INVALID",
        "evidence_class": "PROSPECTIVE_FIELD_QUALIFICATION_ATTESTATION_NOT_CERTIFICATION",
        "release_id": payload.get("release_id"),
        "environment": payload.get("environment"),
        "site_id": payload.get("site_id"),
        "study_id": payload.get("study_id"),
        "artifact_sha256": payload.get("artifact_sha256"),
        "qualification_status": payload.get("qualification_status"),
        "errors": errors,
        "evidence_digest": calculated,
        "timestamp_policy": "RFC3339 with timezone; evidence must be no older than 180 days",
        "claim_boundary": (
            "This is a non-secret, release-bound field-qualification attestation. It does not certify "
            "infrastructure, cybersecurity, availability, regulatory compliance, model performance, or field safety."
        ),
    }


def load_field_qualification_evidence(path: str | Path | None) -> dict[str, Any]:
    """Load and validate field evidence without echoing its contents."""

    if not path:
        return {
            "status": "MISSING",
            "evidence_class": "PROSPECTIVE_FIELD_QUALIFICATION_ATTESTATION_NOT_CERTIFICATION",
            "errors": ["evidence_file_not_configured"],
        }
    evidence_path = Path(path)
    if not evidence_path.exists() or not evidence_path.is_file():
        return {
            "status": "MISSING",
            "evidence_class": "PROSPECTIVE_FIELD_QUALIFICATION_ATTESTATION_NOT_CERTIFICATION",
            "errors": ["evidence_file_not_found"],
        }
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "status": "INVALID",
            "evidence_class": "PROSPECTIVE_FIELD_QUALIFICATION_ATTESTATION_NOT_CERTIFICATION",
            "errors": ["evidence_file_unreadable_or_invalid_json"],
        }
    report = validate_field_qualification_evidence(payload)
    report["path_configured"] = True
    return report
