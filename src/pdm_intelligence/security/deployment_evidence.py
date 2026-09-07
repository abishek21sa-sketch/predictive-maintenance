"""Validate a non-secret deployment evidence attestation.

The repository cannot prove that a customer's ingress, secret manager, CMMS,
or recovery system exists. It can, however, require a structured operator
attestation before configuration is considered ready for deployment review.
The attestation contains references and timestamps only; it is not a
production certification.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

SCHEMA_VERSION = "PDM-DEPLOYMENT-EVIDENCE.v2"
REQUIRED_CONTROLS = (
    "production_auth",
    "managed_state_backend",
    "tls_termination",
    "observability",
    "backup_and_restore",
    "site_connector",
    "change_control",
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
_USERINFO_URL = re.compile(r"^[a-z][a-z0-9+.-]*://[^/]*:[^/]*@", re.IGNORECASE)
_EVIDENCE_REF = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s]+$", re.IGNORECASE)
_SECRET_QUERY_KEYS = frozenset(
    {"access_token", "api_key", "client_secret", "password", "secret", "token"}
)
_MAX_EVIDENCE_AGE = timedelta(days=30)
_CLOCK_SKEW = timedelta(minutes=5)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def is_sha256_digest(value: Any) -> bool:
    """Return whether a value is a canonical lowercase SHA-256 digest."""

    return isinstance(value, str) and bool(_SHA256_HEX.fullmatch(value.strip()))


def _contains_raw_secret(value: Any, key: str = "") -> bool:
    if isinstance(value, dict):
        return any(
            key_name.lower() in _RAW_SECRET_KEYS or _contains_raw_secret(item, key_name)
            for key_name, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_raw_secret(item, key) for item in value)
    if isinstance(value, str):
        return bool(_USERINFO_URL.match(value) or "PRIVATE KEY-----" in value.upper())
    return False


def _canonical_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(canonical).hexdigest()


def _timestamp_error(value: Any, field: str, now: datetime) -> str | None:
    """Return a stable validation error for an RFC3339 timestamp."""

    if not isinstance(value, str) or not value.strip():
        return None
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


def _evidence_ref_error(value: Any, field: str) -> str | None:
    """Require a non-secret URI reference to an operator-held evidence record."""

    if not isinstance(value, str) or not value.strip():
        return None
    reference = value.strip()
    if not _EVIDENCE_REF.fullmatch(reference):
        return f"invalid_{field}_evidence_ref"
    parsed = urlsplit(reference)
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.scheme.lower() in {"data", "file"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        return f"invalid_{field}_evidence_ref"
    try:
        query_keys = {
            key.lower()
            for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
        }
    except ValueError:
        return f"invalid_{field}_evidence_ref"
    if query_keys & _SECRET_QUERY_KEYS:
        return f"secret_bearing_{field}_evidence_ref"
    return None


def validate_deployment_evidence(payload: Any) -> dict[str, Any]:
    """Return a non-secret validation report for an evidence attestation."""

    errors: list[str] = []
    now = datetime.now(UTC)
    if not isinstance(payload, dict):
        return {
            "status": "INVALID",
            "evidence_class": "DEPLOYMENT_EVIDENCE_ATTESTATION_NOT_CERTIFICATION",
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
        "attested_by",
        "attested_at",
    ):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            errors.append(f"missing_{field}")
    if payload.get("artifact_sha256") and not is_sha256_digest(payload["artifact_sha256"]):
        errors.append("invalid_artifact_sha256")
    timestamp_error = _timestamp_error(payload.get("attested_at"), "attested_at", now)
    if timestamp_error:
        errors.append(timestamp_error)
    controls = payload.get("controls")
    if not isinstance(controls, dict):
        errors.append("controls_must_be_object")
        controls = {}
    for control in REQUIRED_CONTROLS:
        item = controls.get(control)
        if not isinstance(item, dict):
            errors.append(f"missing_control_{control}")
            continue
        if item.get("status") != "verified":
            errors.append(f"control_not_verified_{control}")
        for field in ("owner", "verified_at", "evidence_ref"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"missing_{control}_{field}")
        timestamp_error = _timestamp_error(item.get("verified_at"), f"{control}_verified_at", now)
        if timestamp_error:
            errors.append(timestamp_error)
        reference_error = _evidence_ref_error(item.get("evidence_ref"), control)
        if reference_error:
            errors.append(reference_error)
    digest_input = {key: value for key, value in payload.items() if key != "evidence_digest"}
    calculated = _canonical_digest(digest_input)
    supplied = payload.get("evidence_digest")
    if not isinstance(supplied, str) or not supplied.strip():
        errors.append("missing_evidence_digest")
    elif supplied != calculated:
        errors.append("evidence_digest_mismatch")
    return {
        "status": "PASS_ATTESTATION" if not errors else "INVALID",
        "evidence_class": "DEPLOYMENT_EVIDENCE_ATTESTATION_NOT_CERTIFICATION",
        "release_id": payload.get("release_id"),
        "environment": payload.get("environment"),
        "artifact_sha256": payload.get("artifact_sha256"),
        "attested_by": payload.get("attested_by"),
        "attested_at": payload.get("attested_at"),
        "verified_controls": [
            control for control in REQUIRED_CONTROLS
            if isinstance(controls.get(control), dict) and controls[control].get("status") == "verified"
        ],
        "errors": errors,
        "evidence_digest": calculated,
        "timestamp_policy": "RFC3339 with timezone; attestation and control evidence must be no older than 30 days",
        "claim_boundary": (
            "This is a non-secret operator attestation for deployment review. It does not certify "
            "infrastructure, cybersecurity, availability, compliance, model performance, or field safety."
        ),
    }


def load_deployment_evidence(path: str | Path | None) -> dict[str, Any]:
    """Load and validate an evidence file without echoing its contents."""

    if not path:
        return {
            "status": "MISSING",
            "evidence_class": "DEPLOYMENT_EVIDENCE_ATTESTATION_NOT_CERTIFICATION",
            "errors": ["evidence_file_not_configured"],
        }
    evidence_path = Path(path)
    if not evidence_path.exists() or not evidence_path.is_file():
        return {
            "status": "MISSING",
            "evidence_class": "DEPLOYMENT_EVIDENCE_ATTESTATION_NOT_CERTIFICATION",
            "errors": ["evidence_file_not_found"],
        }
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "status": "INVALID",
            "evidence_class": "DEPLOYMENT_EVIDENCE_ATTESTATION_NOT_CERTIFICATION",
            "errors": ["evidence_file_unreadable_or_invalid_json"],
        }
    report = validate_deployment_evidence(payload)
    report["path_configured"] = True
    return report
