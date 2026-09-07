"""Authentication and role-based authorization boundaries.

Local development remains explicit and frictionless. A deployed instance
should set ``PDM_AUTH_MODE=production`` and provide ``PDM_API_KEYS`` through a
secret manager or API gateway. The key format is backward compatible with
``role:key`` and also supports named principals via ``subject:role:key``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

VALID_ROLES = frozenset(
    {
        "viewer",
        "analyst",
        "planner",
        "technician",
        "approver",
        "data-engineer",
        "data-scientist",
        "auditor",
        "service",
        "admin",
    }
)
MIN_PRODUCTION_API_KEY_LENGTH = 32

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"read:operational"}),
    "analyst": frozenset({"read:operational", "run:prediction", "run:anomaly"}),
    "planner": frozenset({"read:operational", "run:prediction", "run:optimization", "run:decision"}),
    "technician": frozenset({"read:operational", "update:work-order"}),
    "approver": frozenset({"read:operational", "approve:decision", "update:work-order"}),
    "data-engineer": frozenset({"read:operational", "ingest:data"}),
    "data-scientist": frozenset({"read:operational", "run:prediction", "train:model"}),
    "auditor": frozenset({"read:operational", "read:audit", "export:audit"}),
    "admin": frozenset({"*"}),
    "local-admin": frozenset({"*"}),
    "service": frozenset({"read:operational", "run:prediction", "run:optimization", "run:decision"}),
}


@dataclass(frozen=True)
class Principal:
    """Authenticated caller identity used at the API authorization boundary."""

    subject: str
    role: str
    auth_method: str = "api_key"

    def can(self, permission: str) -> bool:
        permissions = ROLE_PERMISSIONS.get(self.role, frozenset())
        return "*" in permissions or permission in permissions

    def to_dict(self) -> dict[str, str]:
        return {"subject": self.subject, "role": self.role, "auth_method": self.auth_method}


def _configured_principals() -> tuple[dict[str, Principal], bool]:
    raw = os.getenv("PDM_API_KEYS", "").strip()
    principals: dict[str, Principal] = {}
    for item in filter(None, (x.strip() for x in raw.split(","))):
        head, separator, key = item.rpartition(":")
        if not separator or not head or not key:
            continue
        parts = head.split(":", 1)
        if len(parts) == 2:
            subject, role = parts
        else:
            role = parts[0]
            subject = role
        if role in VALID_ROLES and subject and key:
            principals[key] = Principal(subject=subject, role=role)
    return principals, bool(raw)


def principal_configuration_report() -> dict[str, object]:
    """Return non-secret quality checks for a production principal declaration."""

    raw = os.getenv("PDM_API_KEYS", "").strip()
    entries = [item.strip() for item in raw.split(",") if item.strip()]
    seen_keys: set[str] = set()
    valid_entries = 0
    invalid_entries = 0
    duplicate_keys = 0
    weak_keys = 0
    for item in entries:
        head, separator, key = item.rpartition(":")
        parts = head.split(":", 1) if separator else []
        role = parts[1] if len(parts) == 2 else (parts[0] if parts else "")
        subject = parts[0] if len(parts) == 2 else role
        valid = bool(separator and subject and key and role in VALID_ROLES)
        if not valid:
            invalid_entries += 1
            continue
        valid_entries += 1
        if key in seen_keys:
            duplicate_keys += 1
        seen_keys.add(key)
        if len(key) < MIN_PRODUCTION_API_KEY_LENGTH or any(char.isspace() for char in key):
            weak_keys += 1
    return {
        "configured": bool(raw),
        "entry_count": len(entries),
        "valid_entries": valid_entries,
        "invalid_entries": invalid_entries,
        "duplicate_key_entries": duplicate_keys,
        "weak_key_entries": weak_keys,
        "minimum_key_length": MIN_PRODUCTION_API_KEY_LENGTH,
        "ready": bool(entries)
        and valid_entries == len(entries)
        and duplicate_keys == 0
        and weak_keys == 0,
    }


def _local_principal() -> Principal:
    mode = os.getenv("PDM_AUTH_MODE", "local").strip().lower()
    if mode in {"production", "strict", "required", "api_key"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API-key authentication is required but PDM_API_KEYS is not configured",
        )
    return Principal(subject="local-admin", role="local-admin", auth_method="local-development")


def resolve_principal(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Principal:
    """Resolve the caller, preserving a safe explicit local-development mode."""

    principals, configured = _configured_principals()
    if not principals:
        if configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PDM_API_KEYS is configured but contains no valid principals",
            )
        return _local_principal()
    principal = principals.get(x_api_key or "")
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Valid X-API-Key required")
    return principal


def authorize_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
    """Authenticate a request and return the principal subject for audit records."""

    return resolve_principal(x_api_key).subject


def require_principal(*roles: str):
    allowed = frozenset(roles)
    if not allowed:
        raise ValueError("At least one role is required")

    def dependency(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Principal:
        principal = resolve_principal(x_api_key)
        if principal.role not in allowed and principal.role not in {"admin", "local-admin"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": "Role is not permitted for this operation", "required_roles": sorted(allowed)},
            )
        return principal

    return dependency


def require_roles(*roles: str):
    principal_dependency = require_principal(*roles)

    def dependency(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
        return principal_dependency(x_api_key).subject

    return dependency


def require_permissions(*permissions: str):
    required = frozenset(permissions)
    if not required:
        raise ValueError("At least one permission is required")

    def dependency(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> str:
        principal = resolve_principal(x_api_key)
        if not all(principal.can(permission) for permission in required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"message": "Principal lacks required permission", "required_permissions": sorted(required)},
            )
        return principal.subject

    return dependency
