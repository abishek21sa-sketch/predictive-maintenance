"""Managed-state configuration and connectivity preflight."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

SUPPORTED_MANAGED_SCHEMES = frozenset(
    {
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    }
)


class ManagedStateRuntimeError(RuntimeError):
    """Raised when a declared state backend cannot be honored safely."""


def state_runtime_status() -> dict[str, object]:
    """Describe the selected state adapter without exposing connection details."""

    backend = os.getenv("PDM_STATE_BACKEND", "sqlite").strip().lower()
    if backend == "sqlite":
        return {
            "backend": "sqlite",
            "status": "REFERENCE_ONLY",
            "ready": False,
            "reason": "application_state_runtime_is_local_sqlite_only",
        }
    if backend == "managed":
        descriptor = describe_database_url(os.getenv("PDM_DATABASE_URL"))
        adapter = os.getenv("PDM_MANAGED_STATE_ADAPTER", "").strip().lower()
        if not descriptor.valid:
            return {
                "backend": "managed",
                "status": "NOT_READY",
                "ready": False,
                "reason": descriptor.reason,
            }
        if adapter != "sqlalchemy":
            return {
                "backend": "managed",
                "status": "NOT_READY",
                "ready": False,
                "reason": "managed_sqlalchemy_adapter_not_declared",
            }
        return {
            "backend": "managed",
            "status": "MANAGED_ADAPTER_CONFIGURED",
            "ready": True,
            "adapter": "sqlalchemy_postgresql",
            "reason": "application_state_stores_use_managed_sqlalchemy_adapter",
        }
    return {
        "backend": backend or "missing",
        "status": "INVALID",
        "ready": False,
        "reason": "unsupported_state_backend",
    }


def ensure_state_runtime_available() -> None:
    """Fail closed before a stateful operation can fall back to local SQLite."""

    runtime = state_runtime_status()
    if runtime["status"] == "REFERENCE_ONLY":
        return
    if runtime["status"] == "MANAGED_ADAPTER_CONFIGURED":
        # The state connection performs the live bounded connect.  Keeping this
        # preflight configuration-only avoids opening two database connections
        # for every stateful request while still making connection failure a
        # controlled error in connect_state().
        return
    if runtime["status"] != "REFERENCE_ONLY":
        raise ManagedStateRuntimeError(str(runtime["reason"]))


@dataclass(frozen=True)
class ManagedStateDescriptor:
    backend: str
    scheme: str
    host: str
    port: int | None
    database: str
    redacted_url: str
    valid: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def describe_database_url(raw_url: str | None) -> ManagedStateDescriptor:
    """Describe a database URL without exposing user names or credentials."""

    raw = (raw_url or "").strip()
    if not raw:
        return ManagedStateDescriptor(
            backend="unconfigured",
            scheme="",
            host="",
            port=None,
            database="",
            redacted_url="",
            valid=False,
            reason="missing_database_url",
        )
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        database = parsed.path.lstrip("/").split("?", 1)[0]
        port = parsed.port
    except ValueError:
        return ManagedStateDescriptor(
            backend="unknown",
            scheme="",
            host="",
            port=None,
            database="",
            redacted_url="<invalid-url>",
            valid=False,
            reason="invalid_database_url",
        )

    if scheme in {"sqlite", "sqlite+pysqlite"}:
        reason = "sqlite_is_local_reference_only"
        backend = "sqlite"
        valid = False
    elif scheme in SUPPORTED_MANAGED_SCHEMES and host and database:
        reason = "supported_managed_sqlalchemy_url"
        backend = "managed"
        valid = True
    else:
        reason = "unsupported_or_incomplete_managed_url"
        backend = "unknown"
        valid = False

    host_part = host
    if port is not None:
        host_part = f"{host}:{port}"
    redacted = f"{scheme}://{host_part}/{database}" if scheme else "<invalid-url>"
    return ManagedStateDescriptor(
        backend=backend,
        scheme=scheme,
        host=host,
        port=port,
        database=database,
        redacted_url=redacted,
        valid=valid,
        reason=reason,
    )


def managed_state_declared() -> bool:
    """Return whether the managed adapter boundary is explicitly declared."""

    descriptor = describe_database_url(os.getenv("PDM_DATABASE_URL"))
    return (
        os.getenv("PDM_STATE_BACKEND", "sqlite").strip().lower() == "managed"
        and descriptor.valid
        and os.getenv("PDM_MANAGED_STATE_ADAPTER", "").strip().lower() == "sqlalchemy"
    )


def managed_state_connectivity(raw_url: str | None = None) -> dict[str, object]:
    """Perform a bounded ``SELECT 1`` preflight and return non-secret evidence."""

    url = raw_url if raw_url is not None else os.getenv("PDM_DATABASE_URL")
    descriptor = describe_database_url(url)
    result: dict[str, object] = {"descriptor": descriptor.to_dict(), "status": "NOT_READY"}
    if not descriptor.valid:
        result["reason"] = descriptor.reason
        return result
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        result["reason"] = "sqlalchemy_unavailable"
        return result
    engine = None
    try:
        normalized_url = (url or "").strip()
        if normalized_url.startswith("postgresql://"):
            normalized_url = "postgresql+psycopg://" + normalized_url[len("postgresql://"):]
        engine = create_engine(
            normalized_url,
            pool_pre_ping=True,
            pool_timeout=3,
            connect_args={"connect_timeout": 3},
        )
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        result["status"] = "PASS_MANAGED_ADAPTER"
        result["reason"] = "managed_database_connection_succeeded"
    except Exception:  # noqa: BLE001 - driver/host errors must not leak secrets
        result["reason"] = "connection_failed_or_driver_unavailable"
    finally:
        if engine is not None:
            engine.dispose()
    return result
