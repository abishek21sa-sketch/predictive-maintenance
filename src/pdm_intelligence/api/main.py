from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from pdm_intelligence import __version__
from pdm_intelligence.decision.intelligence import (
    build_decision_intelligence_plan,
    stress_test_decision_intelligence,
)
from pdm_intelligence.external.catalog import ExternalCatalog
from pdm_intelligence.external.gateway import DataGateway
from pdm_intelligence.external.model_lifecycle import (
    predict_external_rul,
    score_fd001_compatible,
    train_external_rul,
)
from pdm_intelligence.fourx.belief_maint_decision import build_belief_maint_decision
from pdm_intelligence.governance.audit import AuditStore
from pdm_intelligence.integrations.cmms import (
    CONTRACT_VERSION as CMMS_CONTRACT_VERSION,
)
from pdm_intelligence.integrations.cmms import (
    list_cmms_work_orders,
    reconcile_work_orders,
    work_orders_jsonl,
)
from pdm_intelligence.integrations.inventory import (
    CONTRACT_VERSION as CMMS_INVENTORY_CONTRACT_VERSION,
)
from pdm_intelligence.integrations.inventory import (
    inventory_jsonl,
    list_cmms_inventory,
    reconcile_inventory,
)
from pdm_intelligence.models.model_registry import ModelRegistry
from pdm_intelligence.models.runtime import score_trajectory
from pdm_intelligence.monitoring.drift import feature_drift_report
from pdm_intelligence.monitoring.runtime import runtime_metrics
from pdm_intelligence.operations.command import (
    ModelPromotionBlockedError,
    build_metropt_operations_case,
    commit_metropt_work_order,
    list_work_orders,
    resource_scenario_from_name,
    update_work_order_status,
)
from pdm_intelligence.optimization.maintenance import (
    AssetMaintenanceInput,
    optimize_maintenance,
    optimize_maintenance_stochastic,
)
from pdm_intelligence.planner.casebook import (
    asset_dossier,
    commitment_ledger,
    fleet_casebook,
    intervention_options,
)
from pdm_intelligence.planner.horizon import (
    build_intervention_horizon,
    compare_intervention_scenarios,
)
from pdm_intelligence.real_data.metropt import (
    METROPT_DATASET_ID,
    METROPT_FAILURE_EVENTS,
    load_metropt_runtime,
)
from pdm_intelligence.reliability.fmea import (
    prioritize_failure_modes,
    turbofan_fd001_fmea,
)
from pdm_intelligence.reliability.kpis import compute_maintenance_kpis
from pdm_intelligence.security.auth import (
    require_permissions,
    require_principal,
    resolve_principal,
)
from pdm_intelligence.security.production_readiness import production_readiness
from pdm_intelligence.service.pipeline import run_demo
from pdm_intelligence.storage.database import managed_state_schema_check
from pdm_intelligence.storage.managed_state import (
    ManagedStateRuntimeError,
    ensure_state_runtime_available,
    managed_state_connectivity,
    state_runtime_status,
)
from pdm_intelligence.storage.registry import AssetRegistry

app = FastAPI(
    title="Predictive Maintenance Intelligence API",
    version=__version__,
    description="Reliability, prognostics and maintenance decision intelligence service",
)


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _requires_promoted_real_model() -> bool:
    """Require a passed real-data model gate for production operational actions."""

    return (
        os.getenv("PDM_AUTH_MODE", "local").strip().lower() == "production"
        or _truthy("PDM_REQUIRE_PROMOTED_REAL_DATA_MODEL")
    )


def _enforce_promoted_real_model(case: dict) -> None:
    model_ready = case.get("model_status") == "PROMOTION_READY"
    integrity_ready = case.get("runtime_integrity", {}).get("passed") is True
    if _requires_promoted_real_model() and (not model_ready or not integrity_ready):
        blocked_code = (
            "REAL_MODEL_PROMOTION_BLOCKED" if not model_ready else "REAL_RUNTIME_INTEGRITY_BLOCKED"
        )
        message = (
            "Production operational decisions require a passed real-data model promotion gate."
            if not model_ready
            else "Production operational decisions require verified MetroPT runtime artifact integrity."
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": blocked_code,
                "message": message,
                "model_status": case.get("model_status"),
                "runtime_integrity": case.get("runtime_integrity"),
                "claim_boundary": "Historical/testing evidence remains available only outside the production action boundary.",
            },
        )


@app.exception_handler(ManagedStateRuntimeError)
async def managed_state_runtime_error_handler(request: Request, exc: ManagedStateRuntimeError):
    """Return a safe 503 instead of silently serving state from local SQLite."""

    return JSONResponse(
        status_code=503,
        content={
            "error_code": "MANAGED_STATE_RUNTIME_UNAVAILABLE",
            "detail": "Managed state is declared, but the configured managed adapter is unavailable.",
            "reason": str(exc),
            "claim_boundary": "No stateful operation was executed against a local fallback store.",
        },
    )

_logger = logging.getLogger("pdm_intelligence.api")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_PUBLIC_API_PATHS = frozenset({"/api/health", "/api/health/ready"})


def _request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "")
    return supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex


def _route_label(request: Request) -> str:
    """Return a route template without putting user input into metric labels."""

    route = getattr(request.scope.get("route"), "path", None) or request.url.path
    trusted_route = getattr(request.scope.get("route"), "path", None)
    if trusted_route:
        return route if len(route) <= 160 else "/unclassified"
    # Unmatched requests have no trusted route template. Keep them in one
    # bounded bucket so arbitrary path segments cannot create metric series.
    if route in {"/", "/metrics", "/api/health", "/api/health/ready"}:
        return route
    return "/unmatched"


@app.middleware("http")
async def api_authentication(request: Request, call_next) -> Response:
    """Authenticate every API route except bounded orchestration liveness probes.

    Individual routes still enforce their role/permission requirements. This
    boundary prevents a route from becoming accidentally anonymous merely
    because a developer omitted a dependency while adding a new API surface.
    The liveness endpoints disclose no operational data and must remain usable
    by a container orchestrator before application credentials are available.
    """

    if request.url.path.startswith("/api/") and request.url.path not in _PUBLIC_API_PATHS:
        try:
            resolve_principal(request.headers.get("X-API-Key"))
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


@app.middleware("http")
async def runtime_observability(request: Request, call_next) -> Response:
    request_id = _request_id(request)
    request.state.request_id = request_id
    route_label = _route_label(request)
    started = time.perf_counter()
    runtime_metrics.request_started()
    try:
        response = await call_next(request)
    except Exception:
        duration = time.perf_counter() - started
        runtime_metrics.request_finished(request.method, route_label, 500, duration)
        _logger.exception(
            "api_request_failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "route": route_label,
                "status_code": 500,
                "duration_ms": round(duration * 1000, 3),
            },
        )
        raise

    runtime_metrics.request_finished(
        request.method, route_label, response.status_code, time.perf_counter() - started
    )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
    if _truthy("PDM_TLS_TERMINATED"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/") or request.url.path == "/metrics":
        response.headers["Cache-Control"] = "no-store"
    return response


class OptimizeRequest(BaseModel):
    assets: list[dict]
    horizon: int = Field(default=30, ge=1, le=365)
    capacity_per_cycle: int = Field(default=2, ge=1, le=50)
    labor_hours_per_cycle: float | None = Field(default=None, gt=0)
    spares_per_cycle: int | None = Field(default=None, ge=1)
    skill_capacity_per_cycle: dict[str, int] | None = None
    part_inventory: dict[str, int] | None = None


class BeliefMaintRequest(BaseModel):
    transition_stress: float = Field(default=1.0, ge=0.5, le=3.0)
    crew_capacity: int = Field(default=1, ge=1, le=5)
    opportunity_cost_scale: float = Field(default=1.0, ge=0.25, le=3.0)


class StochasticOptimizeRequest(OptimizeRequest):
    rul_scenarios: list[dict[int, float]]
    probabilities: list[float] | None = None


class KPIRequest(BaseModel):
    events: list[dict]
    operating_cycles: float = Field(gt=0)


class PredictRequest(BaseModel):
    records: list[dict]


class DriftRequest(BaseModel):
    reference: list[dict]
    current: list[dict]
    columns: list[str] | None = None




class ExternalTextFile(BaseModel):
    entity_type: str
    filename: str
    content: str


class ExternalTextIngestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    files: list[ExternalTextFile]
    mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    units: dict[str, dict[str, str]] = Field(default_factory=dict)


class ExternalPathFile(BaseModel):
    entity_type: str
    path: str


class ExternalPathIngestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    files: list[ExternalPathFile]
    mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    units: dict[str, dict[str, str]] = Field(default_factory=dict)


class ExternalSQLSource(BaseModel):
    entity_type: str
    connection_url: str
    table: str | None = None
    query: str | None = None


class ExternalSQLIngestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sources: list[ExternalSQLSource]
    mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    units: dict[str, dict[str, str]] = Field(default_factory=dict)


class ExternalTrainRequest(BaseModel):
    dataset_id: str
    seed: int = 42
    validation_fraction: float = Field(default=0.25, gt=0.05, lt=0.5)


class ExternalPredictRequest(BaseModel):
    dataset_id: str
    model_id: str


class ModelPromotionRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class AssetRegistration(BaseModel):
    asset_id: int = Field(gt=0)
    asset_type: str = "turbofan"
    status: str = "active"
    installed_cycle: int = Field(default=0, ge=0)
    metadata: dict = Field(default_factory=dict)


class MetroPTCommitRequest(BaseModel):
    at: str | None = None
    scenario: str = "baseline"
    bays: int = Field(default=2, ge=1, le=6)


class WorkOrderStatusRequest(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=500)


class CMMSWorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_order_id: str = Field(min_length=1, max_length=120)
    asset_id: str = Field(min_length=1, max_length=120)
    status: str = Field(min_length=1, max_length=40)
    planned_start: str = Field(min_length=1, max_length=80)
    duration_hours: float = Field(gt=0, le=8760)
    required_skill: str = Field(min_length=1, max_length=120)
    part_id: str = Field(min_length=1, max_length=120)
    part_qty: int = Field(ge=0, le=1_000_000)
    source_system: str | None = Field(default=None, min_length=1, max_length=80)
    action: str | None = Field(default=None, max_length=120)
    scheduled_cycle: int | None = Field(default=None, ge=0)
    decision_id: str | None = Field(default=None, max_length=120)
    approval_id: str | None = Field(default=None, max_length=120)
    external_revision: int | None = Field(default=None, ge=0)
    source_updated_at: str | None = Field(default=None, max_length=80)


class CMMSBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: str = Field(min_length=1, max_length=80)
    records: list[CMMSWorkOrderRequest] = Field(min_length=1, max_length=10_000)


class CMMSInventoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=240)
    on_hand: float = Field(ge=0)
    reserved: float = Field(ge=0)
    unit_cost: float = Field(ge=0)
    location: str | None = Field(default=None, max_length=120)
    warehouse_location: str | None = Field(default=None, max_length=120)
    source_system: str | None = Field(default=None, min_length=1, max_length=80)
    external_revision: int | None = Field(default=None, ge=0)
    source_updated_at: str | None = Field(default=None, max_length=80)


class CMMSInventoryBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: str = Field(min_length=1, max_length=80)
    records: list[CMMSInventoryRequest] = Field(min_length=1, max_length=10_000)


class DecisionApprovalRequest(BaseModel):
    decision_id: str = Field(pattern=r"^BELIEF-[A-F0-9]{16}$")
    action: Literal["approve", "reject"]
    rationale: str = Field(min_length=3, max_length=1000)


@lru_cache(maxsize=1)
def demo():
    return run_demo()


@lru_cache(maxsize=1)
def release_snapshot():
    external = Path("artifacts/reports/fd001_operational_snapshot.json")
    bundled = Path(__file__).resolve().parents[1] / "release" / "fd001_operational_snapshot.json"
    path = external if external.exists() else bundled
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _state_db_path() -> Path:
    ensure_state_runtime_available()
    return Path(os.getenv("PDM_STATE_DB", "data/pdm.db"))


def _external_data_root() -> Path:
    return Path(os.getenv("PDM_EXTERNAL_DATA_ROOT", "data/external"))


def _external_model_root() -> Path:
    return Path(os.getenv("PDM_EXTERNAL_MODEL_ROOT", "data/external_models"))


def _metropt_root() -> Path:
    return Path(os.getenv("PDM_METROPT_ROOT", "data/external/metropt3"))


def registry() -> AssetRegistry:
    return AssetRegistry(_state_db_path())


def audit() -> AuditStore:
    return AuditStore(_state_db_path())


def data_gateway() -> DataGateway:
    return DataGateway(_external_data_root(), _state_db_path())


def external_catalog() -> ExternalCatalog:
    return ExternalCatalog(_state_db_path())


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "predictive-maintenance-intelligence", "version": __version__}


@app.get("/api/health/ready")
def readiness():
    """Expose a bounded runtime readiness check for container orchestration."""

    production_mode = os.getenv("PDM_AUTH_MODE", "local").strip().lower() == "production"
    if production_mode or _truthy("PDM_REQUIRE_PRODUCTION_READINESS"):
        deployment = production_readiness()
        if deployment["status"] != "READY_FOR_DEPLOYMENT_REVIEW":
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "reason": "production_readiness_gate_blocked",
                    "blockers": deployment.get("blockers", []),
                    "claim_boundary": deployment.get("claim_boundary"),
                },
            )

    runtime = state_runtime_status()
    if runtime["status"] == "REFERENCE_ONLY":
        return {
            "status": "ready",
            "backend": "sqlite_reference",
            "claim_boundary": "Reference readiness does not certify production deployment.",
        }
    if runtime["status"] != "MANAGED_ADAPTER_CONFIGURED":
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "managed_state_runtime_not_configured",
                "claim_boundary": "No local state fallback is used.",
            },
        )
    connectivity = managed_state_connectivity()
    if connectivity.get("status") != "PASS_MANAGED_ADAPTER":
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "managed_database_unavailable",
                "claim_boundary": "No local state fallback is used.",
            },
        )
    schema = managed_state_schema_check()
    if schema.get("status") != "PASS_MANAGED_SCHEMA":
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "managed_state_schema_not_ready",
                "schema_reason": schema.get("reason", "managed_schema_not_ready"),
                "claim_boundary": "Managed runtime readiness requires an explicitly migrated and verified schema; no local state fallback is used.",
            },
        )
    return {
        "status": "ready",
        "backend": "managed_postgresql",
        "connectivity": "PASS_MANAGED_ADAPTER",
        "claim_boundary": "Runtime readiness is not a production certification.",
    }


@app.get("/api/health/production-readiness")
def production_readiness_status():
    """Expose the fail-closed deployment gate without exposing secrets."""

    return production_readiness()


@app.get("/metrics", response_class=PlainTextResponse)
def metrics(actor: str = Depends(require_permissions("read:operational"))):
    """Expose low-cardinality process metrics for an authenticated scraper."""

    return PlainTextResponse(
        runtime_metrics.prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/portfolio")
def portfolio():
    snap = release_snapshot()
    if snap is not None:
        return {
            "data_mode": snap["data_mode"],
            "dataset": snap["dataset"],
            "model_metrics": snap["model_metrics"],
            "baseline_metrics": {"note": "See fd001_benchmark.json for age-only baseline"},
            "reliability": snap["reliability"],
            "simulation": snap["simulation"],
            "simulation_optimization": snap["simulation_optimization"],
            "asset_count": snap["asset_count"],
            "selected_model": snap.get("selected_model", "validated_release_model"),
            "top_features": snap["top_features"],
        }
    r = demo()
    return {
        "data_mode": "deterministic_ci_fixture",
        "model_metrics": r.metrics,
        "baseline_metrics": r.baseline_metrics,
        "reliability": r.reliability,
        "simulation": r.simulation,
        "simulation_optimization": r.simulation_optimization,
        "asset_count": len(r.predictions),
        "selected_model": r.selected_model,
        "top_features": r.top_features,
    }


@app.get("/api/assets")
def assets():
    snap = release_snapshot()
    if snap is not None:
        return snap["assets"]
    r = demo()
    decision_by_asset = {d["asset_id"]: d for d in r.decisions}
    return [p | {"decision": decision_by_asset[int(p["unit_id"])]} for p in r.predictions]


@app.get("/api/decisions")
def decisions():
    snap = release_snapshot()
    return snap["decisions"] if snap is not None else demo().decisions




@app.post("/api/predict")
def predict(req: PredictRequest, actor: str = Depends(require_permissions("run:prediction"))):
    try:
        result = score_trajectory(req.records)
        audit().append("prediction", actor, {"asset_count": len(result)})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.get("/api/reliability/fmea")
def fmea():
    return prioritize_failure_modes(turbofan_fd001_fmea())


@app.post("/api/reliability/kpis")
def kpis(req: KPIRequest, actor: str = Depends(require_permissions("run:prediction"))):
    return compute_maintenance_kpis(req.events, req.operating_cycles).to_dict()


@app.post("/api/optimize")
def optimize(req: OptimizeRequest, actor: str = Depends(require_permissions("run:optimization"))):
    items = [AssetMaintenanceInput(**x) for x in req.assets]
    result = [x.__dict__ for x in optimize_maintenance(items, req.horizon, req.capacity_per_cycle, req.labor_hours_per_cycle, req.spares_per_cycle, req.skill_capacity_per_cycle, req.part_inventory)]
    audit().append("optimization", actor, {"asset_count": len(items), "stochastic": False})
    return result


@app.post("/api/optimize/stochastic")
def optimize_stochastic(req: StochasticOptimizeRequest, actor: str = Depends(require_permissions("run:optimization"))):
    items = [AssetMaintenanceInput(**x) for x in req.assets]
    result = [x.__dict__ for x in optimize_maintenance_stochastic(
        items, req.rul_scenarios, req.probabilities, req.horizon, req.capacity_per_cycle, req.labor_hours_per_cycle, req.spares_per_cycle, req.skill_capacity_per_cycle, req.part_inventory
    )]
    audit().append("optimization", actor, {"asset_count": len(items), "stochastic": True, "scenario_count": len(req.rul_scenarios)})
    return result




@app.get("/api/casebook")
def casebook(limit: int = 24):
    snap = release_snapshot()
    source = snap["assets"] if snap is not None else demo().predictions
    return fleet_casebook(source, min(max(limit, 1), 100))


@app.get("/api/assets/{asset_id}/dossier")
def dossier(asset_id: int):
    snap = release_snapshot()
    source = snap["assets"] if snap is not None else demo().predictions
    try:
        return asset_dossier(source, asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/assets/{asset_id}/intervention-options")
def options(asset_id: int):
    snap = release_snapshot()
    source = snap["assets"] if snap is not None else demo().predictions
    try:
        return intervention_options(source, asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/commitment-ledger")
def ledger(horizon: int = 30, bay_capacity: int = 2, labor_hours_per_cycle: float = 24.0, scenario: str = "baseline"):
    snap = release_snapshot()
    source = snap["assets"] if snap is not None else demo().predictions
    try:
        return commitment_ledger(source, horizon=horizon, bay_capacity=bay_capacity, labor_hours_per_cycle=labor_hours_per_cycle, scenario=scenario)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.get("/api/planner/horizon")
def planner_horizon(
    horizon: int = 30,
    bay_capacity: int = 2,
    labor_hours_per_cycle: float = 24.0,
    scenario: str = "baseline",
):
    snap = release_snapshot()
    source = snap["assets"] if snap is not None else demo().predictions
    try:
        return build_intervention_horizon(
            source, horizon=horizon, bay_capacity=bay_capacity,
            labor_hours_per_cycle=labor_hours_per_cycle, scenario=scenario,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/planner/scenarios")
def planner_scenarios(horizon: int = 30, bay_capacity: int = 2, labor_hours_per_cycle: float = 24.0):
    snap = release_snapshot()
    source = snap["assets"] if snap is not None else demo().predictions
    try:
        return compare_intervention_scenarios(
            source, horizon=horizon, bay_capacity=bay_capacity,
            labor_hours_per_cycle=labor_hours_per_cycle,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc



@app.get("/api/decision-intelligence/plan")
def decision_intelligence_plan(
    horizon: int = 30,
    bay_capacity: int = 2,
    labor_hours_per_cycle: float = 24.0,
    risk_aversion: float = 0.15,
    cvar_alpha: float = 0.90,
    n_rul_scenarios: int = 9,
    seed: int = 4401,
):
    snap = release_snapshot()
    source = snap["assets"] if snap is not None else demo().predictions
    try:
        return build_decision_intelligence_plan(
            source, horizon=horizon, bay_capacity=bay_capacity,
            labor_hours_per_cycle=labor_hours_per_cycle, risk_aversion=risk_aversion,
            cvar_alpha=cvar_alpha, n_rul_scenarios=n_rul_scenarios, seed=seed,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/decision-intelligence/stress-test")
def decision_intelligence_stress_test(
    horizon: int = 30,
    bay_capacity: int = 2,
    labor_hours_per_cycle: float = 24.0,
    risk_aversion: float = 0.15,
    cvar_alpha: float = 0.90,
    n_rul_scenarios: int = 9,
    plan_seed: int = 4401,
    n_simulations: int = 300,
    simulation_seed: int = 9917,
):
    snap = release_snapshot()
    source = snap["assets"] if snap is not None else demo().predictions
    try:
        return stress_test_decision_intelligence(
            source, horizon=horizon, bay_capacity=bay_capacity,
            labor_hours_per_cycle=labor_hours_per_cycle, risk_aversion=risk_aversion,
            cvar_alpha=cvar_alpha, n_rul_scenarios=n_rul_scenarios,
            plan_seed=plan_seed, n_simulations=n_simulations, simulation_seed=simulation_seed,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/real-data/metropt/status")
def metropt_status():
    runtime = load_metropt_runtime(_metropt_root())
    if runtime is None:
        return {
            "dataset_id": METROPT_DATASET_ID,
            "status": "NOT_READY",
            "source_status": "NOT_READY",
            "model_status": "NOT_EVALUATED",
            "real_data": True,
            "raw_data_packaged": False,
            "next_action": "Run python scripts/prepare_metropt3.py --download",
            "failure_events": [x.__dict__ for x in METROPT_FAILURE_EVENTS],
            "claim_boundary": "No synthetic substitute is displayed as MetroPT-3 real operational evidence.",
        }
    quality_gate = runtime.get("model", {}).get("quality_gate", {})
    integrity = runtime.get("integrity_check", {})
    integrity_ready = (
        integrity.get("passed") is True
        if "integrity" in runtime
        else not _requires_promoted_real_model()
    )
    model_ready = bool(
        integrity_ready
        and quality_gate.get("passed") is True
        and quality_gate.get("promotion_allowed") is True
    )
    if not integrity_ready:
        status = "RUNTIME_INTEGRITY_BLOCKED"
    else:
        status = "READY" if model_ready else "MODEL_GATE_BLOCKED"
    return {
        "dataset_id": METROPT_DATASET_ID,
        "status": status,
        "source_status": "READY" if integrity_ready else "NOT_READY",
        "model_status": "PROMOTION_READY" if model_ready else "PROMOTION_BLOCKED",
        "runtime_integrity": integrity,
        "real_data": True,
        "dataset": runtime["dataset"],
        "model": runtime["model"],
        "feature_store": runtime["feature_store"],
        "next_action": (
            None
            if model_ready
            else "Rebuild MetroPT runtime evidence; a missing or modified declared artifact is not trusted."
            if not integrity_ready
            else "Historical MetroPT-3 evidence is available, but production model promotion is blocked until validation, future-holdout and independent-event coverage gates pass."
        ),
        "claim_boundary": runtime["claim_boundary"],
    }


@app.get("/api/operations/metropt/case")
def metropt_operations_case(at: str | None = None, scenario: str = "baseline", bays: int = 2):
    try:
        profile = resource_scenario_from_name(scenario, bays=bays)
        case = build_metropt_operations_case(_metropt_root(), _state_db_path(), at=at, scenario=profile)
        _enforce_promoted_real_model(case)
        return case
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/operations/work-orders")
def operations_work_orders():
    return list_work_orders(_state_db_path())


@app.post("/api/operations/metropt/work-orders/commit")
def operations_commit_metropt(req: MetroPTCommitRequest, actor: str = Depends(require_permissions("run:decision"))):
    try:
        profile = resource_scenario_from_name(req.scenario, bays=req.bays)
        result = commit_metropt_work_order(
            _metropt_root(),
            _state_db_path(),
            at=req.at,
            scenario=profile,
            actor=actor,
            require_promoted_model=_requires_promoted_real_model(),
        )
        audit().append("metropt_work_order_commit", actor, result, subject=result["work_order_id"])
        return result
    except ModelPromotionBlockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "REAL_MODEL_PROMOTION_BLOCKED",
                "message": str(exc),
                "claim_boundary": "Production work-order commitment is disabled until the real-data model gate passes.",
            },
        ) from exc
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/operations/work-orders/{work_order_id}/status")
def operations_update_work_order_status(
    work_order_id: str,
    req: WorkOrderStatusRequest,
    actor: str = Depends(require_permissions("update:work-order")),
):
    try:
        result = update_work_order_status(
            _state_db_path(), work_order_id, req.status, actor=actor, note=req.note
        )
        audit().append(
            "work_order_status_change",
            actor,
            {"work_order_id": work_order_id, "status": result["status"]},
            subject=work_order_id,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/integrations/cmms/work-orders")
def cmms_work_orders(
    source_system: str | None = None,
    actor: str = Depends(require_permissions("read:operational")),
):
    return {
        "contract_version": CMMS_CONTRACT_VERSION,
        "source_system": source_system,
        "records": list_cmms_work_orders(_state_db_path(), source_system=source_system),
        "claim_boundary": "CMMS records describe external exchange state; they do not prove field maintenance occurred.",
    }


@app.post("/api/integrations/cmms/reconcile")
def cmms_reconcile(
    req: CMMSBatchRequest,
    actor: str = Depends(require_permissions("ingest:data")),
):
    try:
        return reconcile_work_orders(
            [record.model_dump(exclude_none=True) for record in req.records],
            _state_db_path(),
            source_system=req.source_system,
            actor=actor,
            audit_store=audit(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/integrations/cmms/export")
def cmms_export(
    req: CMMSBatchRequest,
    actor: str = Depends(require_permissions("ingest:data")),
):
    try:
        content, batch_id = work_orders_jsonl(
            [record.model_dump(exclude_none=True) for record in req.records],
            source_system=req.source_system,
        )
        content_sha256 = sha256(content.encode("utf-8")).hexdigest()
        audit_event_id = audit().append(
            "cmms_work_order_export",
            actor,
            {
                "contract_version": CMMS_CONTRACT_VERSION,
                "source_system": req.source_system,
                "batch_id": batch_id,
                "record_count": len(req.records),
                "content_sha256": content_sha256,
                "claim_boundary": "Exported CMMS records are an exchange artifact, not proof of field execution.",
            },
            subject=batch_id,
        )
        return PlainTextResponse(
            content,
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": 'attachment; filename="cmms-work-orders.jsonl"',
                "X-Contract-Version": CMMS_CONTRACT_VERSION,
                "X-Exchange-Batch-ID": batch_id,
                "X-Exchange-Content-SHA256": content_sha256,
                "X-Audit-Event-ID": str(audit_event_id),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/integrations/cmms/inventory")
def cmms_inventory(
    source_system: str | None = None,
    actor: str = Depends(require_permissions("read:operational")),
):
    return {
        "contract_version": CMMS_INVENTORY_CONTRACT_VERSION,
        "source_system": source_system,
        "records": list_cmms_inventory(_state_db_path(), source_system=source_system),
        "claim_boundary": "CMMS inventory records describe external stock state; they do not prove physical stock without a site-controlled count process.",
    }


@app.post("/api/integrations/cmms/inventory/reconcile")
def cmms_inventory_reconcile(
    req: CMMSInventoryBatchRequest,
    actor: str = Depends(require_permissions("ingest:data")),
):
    try:
        return reconcile_inventory(
            [record.model_dump(exclude_none=True) for record in req.records],
            _state_db_path(),
            source_system=req.source_system,
            actor=actor,
            audit_store=audit(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/integrations/cmms/inventory/export")
def cmms_inventory_export(
    req: CMMSInventoryBatchRequest,
    actor: str = Depends(require_permissions("ingest:data")),
):
    try:
        content, batch_id = inventory_jsonl(
            [record.model_dump(exclude_none=True) for record in req.records],
            source_system=req.source_system,
        )
        content_sha256 = sha256(content.encode("utf-8")).hexdigest()
        audit_event_id = audit().append(
            "cmms_inventory_export",
            actor,
            {
                "contract_version": CMMS_INVENTORY_CONTRACT_VERSION,
                "source_system": req.source_system,
                "batch_id": batch_id,
                "record_count": len(req.records),
                "content_sha256": content_sha256,
                "claim_boundary": "Exported CMMS inventory is an exchange artifact, not proof of physical stock.",
            },
            subject=batch_id,
        )
        return PlainTextResponse(
            content,
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": 'attachment; filename="cmms-inventory.jsonl"',
                "X-Contract-Version": CMMS_INVENTORY_CONTRACT_VERSION,
                "X-Exchange-Batch-ID": batch_id,
                "X-Exchange-Content-SHA256": content_sha256,
                "X-Audit-Event-ID": str(audit_event_id),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/registry/assets")
def registry_assets():
    return registry().list_assets()


@app.post("/api/registry/assets")
def register_asset(req: AssetRegistration, actor: str = Depends(require_permissions("ingest:data"))):
    registry().upsert(req.asset_id, req.asset_type, req.status, req.installed_cycle, req.metadata)
    audit().append("asset_registration", actor, req.model_dump(), subject=str(req.asset_id))
    return {"status": "registered", "asset_id": req.asset_id}


@app.post("/api/monitoring/drift")
def drift(req: DriftRequest, actor: str = Depends(require_permissions("run:anomaly"))):
    return feature_drift_report(pd.DataFrame(req.reference), pd.DataFrame(req.current), req.columns)


@app.get("/api/audit")
def audit_events(limit: int = 100, actor: str = Depends(require_permissions("read:audit"))):
    return audit().list(min(max(limit, 1), 500))


@app.get("/api/audit/verify")
def audit_verify(actor: str = Depends(require_permissions("read:audit"))):
    return audit().verify_chain()


@app.get("/api/audit/export")
def audit_export(limit: int = 10_000, actor: str = Depends(require_permissions("export:audit"))):
    verification = audit().verify_chain()
    if not verification["valid"]:
        raise HTTPException(
            status_code=409,
            detail={"message": "Audit chain verification failed", **verification},
        )
    return PlainTextResponse(
        audit().export_jsonl(min(max(limit, 1), 100_000)),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": 'attachment; filename="pdm-audit-export.jsonl"',
            "X-Audit-Chain-Head": verification["head_hash"],
            "X-Audit-Events": str(verification["checked_events"]),
        },
    )


@app.get("/api/data/modes")
def data_modes():
    metropt_runtime = load_metropt_runtime(_metropt_root())
    quality_gate = (metropt_runtime or {}).get("model", {}).get("quality_gate", {})
    integrity = (metropt_runtime or {}).get("integrity_check", {})
    if metropt_runtime is None:
        metropt_integrity_ready = False
    elif _requires_promoted_real_model():
        metropt_integrity_ready = integrity.get("passed") is True
    else:
        metropt_integrity_ready = "integrity" not in metropt_runtime or integrity.get("passed") is True
    metropt_source_ready = metropt_runtime is not None and metropt_integrity_ready
    metropt_model_ready = bool(
        metropt_source_ready
        and quality_gate.get("passed") is True
        and quality_gate.get("promotion_allowed") is True
    )
    metropt_capability_status = (
        "RUNTIME_INTEGRITY_BLOCKED"
        if metropt_runtime is not None and not metropt_integrity_ready
        else "READY"
        if metropt_model_ready
        else "MODEL_GATE_BLOCKED"
        if metropt_source_ready
        else "NOT_READY"
    )
    modes = [
        {"id": "benchmark", "status": "READY", "description": "Bundled NASA C-MAPSS FD001 reproducible benchmark"},
        {"id": "browser_text", "status": "READY", "formats": ["csv", "json", "jsonl", "ndjson"]},
        {"id": "file_path", "status": "READY", "formats": ["csv", "json", "jsonl", "parquet*"], "note": "*Parquet requires optional parquet engine"},
        {"id": "sql", "status": "READY", "description": "SQLAlchemy database source; database-specific driver may be required"},
        {"id": "historical_replay", "status": "READY", "description": "Replay accepted telemetry by cycle or timestamp"},
        {
            "id": "metropt3_real_operations",
            "status": (
                metropt_capability_status
            ),
            "source_status": "READY" if metropt_source_ready else "NOT_READY",
            "model_status": (
                "PROMOTION_READY"
                if metropt_model_ready
                else "PROMOTION_BLOCKED"
                if metropt_runtime is not None and not metropt_integrity_ready
                else "PROMOTION_BLOCKED"
                if metropt_source_ready
                else "NOT_EVALUATED"
            ),
            "description": "Real MetroPT-3 railway-compressor telemetry and documented failure-window decision workflow",
            "raw_data_packaged": False,
            "next_action": (
                None
                if metropt_model_ready
                else "Rebuild MetroPT runtime evidence; a missing or modified declared artifact is not trusted."
                if metropt_runtime is not None and not metropt_integrity_ready
                else "Historical evidence is available; improve validation, future-holdout and independent-event coverage before model promotion."
                if metropt_source_ready
                else "Run python scripts/prepare_metropt3.py --download"
            ),
        },
    ]
    return {
        "modes": modes,
        "evidence_boundary": (
            "Data readiness is assessed per capability; incompatible datasets never inherit NASA model performance. "
            "MetroPT-3 source readiness is separate from model promotion readiness; a prepared dataset with a failed quality gate is never reported as production READY."
        ),
    }


@app.post("/api/data/preview/text")
def preview_external_text(
    req: ExternalTextIngestRequest,
    actor: str = Depends(require_permissions("ingest:data")),
):
    try:
        return data_gateway().preview_text_bundle(
            [x.model_dump() for x in req.files], req.mappings
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/data/ingest/text")
def ingest_external_text(req: ExternalTextIngestRequest, actor: str = Depends(require_permissions("ingest:data"))):
    try:
        result = data_gateway().ingest_text_bundle(
            req.name, [x.model_dump() for x in req.files], req.mappings, req.units
        )
        audit().append("external_data_ingestion", actor, {"dataset_id": result.dataset_id, "mode": "browser_text"})
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/data/ingest/path")
def ingest_external_paths(req: ExternalPathIngestRequest, actor: str = Depends(require_permissions("ingest:data"))):
    try:
        result = data_gateway().ingest_paths(
            req.name, [x.model_dump() for x in req.files], req.mappings, req.units
        )
        audit().append("external_data_ingestion", actor, {"dataset_id": result.dataset_id, "mode": "file_path"})
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/data/ingest/sql")
def ingest_external_sql(req: ExternalSQLIngestRequest, actor: str = Depends(require_permissions("ingest:data"))):
    try:
        result = data_gateway().ingest_sql(
            req.name, [x.model_dump() for x in req.sources], req.mappings, req.units
        )
        audit().append("external_data_ingestion", actor, {"dataset_id": result.dataset_id, "mode": "sql"})
        return result.to_dict()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/data/datasets")
def external_datasets():
    return external_catalog().list_datasets()


@app.get("/api/data/datasets/{dataset_id}/readiness")
def dataset_readiness(dataset_id: str):
    item = external_catalog().get_dataset(dataset_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return {
        "dataset_id": dataset_id,
        "name": item["name"],
        "evidence_class": item["evidence_class"],
        "readiness": item["readiness"],
        "fingerprint_sha256": item["fingerprint_sha256"],
        "evidence_boundary": item["evidence_boundary"],
    }


@app.get("/api/data/datasets/{dataset_id}/replay")
def dataset_replay(dataset_id: str, upto: str | None = None, limit: int = 1000):
    try:
        parsed: str | float | None = upto
        frames = data_gateway().load_dataset(dataset_id)
        if "cycle" in frames.get("telemetry", pd.DataFrame()).columns and upto is not None:
            parsed = float(upto)
        return data_gateway().replay(dataset_id, parsed, limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/models/external/rul/train")
def train_user_rul(req: ExternalTrainRequest, actor: str = Depends(require_permissions("train:model"))):
    try:
        frames = data_gateway().load_dataset(req.dataset_id)
        readiness = external_catalog().get_dataset(req.dataset_id)
        if readiness is None:
            raise ValueError("Dataset not found")
        status = readiness["readiness"]["capabilities"]["external_rul_training"]["status"]
        if status != "READY":
            missing = readiness["readiness"]["capabilities"]["external_rul_training"]["missing"]
            raise ValueError(f"External RUL training is not ready; missing: {missing}")
        result = train_external_rul(
            frames["telemetry"], req.dataset_id, output_root=_external_model_root(),
            catalog_path=_state_db_path(), seed=req.seed, validation_fraction=req.validation_fraction
        )
        audit().append("external_rul_training", actor, {"dataset_id": req.dataset_id, "model_id": result.model_id})
        return result.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/models/external")
def external_models():
    return external_catalog().list_models()


@app.get("/api/models/registry")
def model_registry(
    dataset_id: str | None = None,
    stage: str | None = None,
    actor: str = Depends(require_permissions("read:operational")),
):
    """List model lifecycle records without exposing model artifacts or secrets."""

    return ModelRegistry(_external_model_root()).list(dataset_id=dataset_id, stage=stage)


@app.post("/api/models/registry/{model_id}/promote")
def promote_model(
    model_id: str,
    req: ModelPromotionRequest,
    actor: str = Depends(require_permissions("approve:decision")),
):
    try:
        return ModelRegistry(_external_model_root()).promote(model_id, actor=actor, reason=req.reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/data/datasets/{dataset_id}/predict/fd001")
def predict_fd001_compatible(dataset_id: str, actor: str = Depends(require_permissions("run:prediction"))):
    try:
        item = external_catalog().get_dataset(dataset_id)
        if item is None:
            raise ValueError("Dataset not found")
        status = item["readiness"]["capabilities"]["fd001_rul_inference"]["status"]
        if status != "READY":
            missing = item["readiness"]["capabilities"]["fd001_rul_inference"]["missing"]
            raise ValueError(f"Bundled FD001 model is not compatible; missing: {missing}")
        frames = data_gateway().load_dataset(dataset_id)
        result = score_fd001_compatible(frames["telemetry"])
        audit().append("fd001_compatible_external_prediction", actor, {"dataset_id": dataset_id, "asset_count": len(result)})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/models/external/rul/predict")
def predict_user_rul(req: ExternalPredictRequest, actor: str = Depends(require_permissions("run:prediction"))):
    try:
        frames = data_gateway().load_dataset(req.dataset_id)
        result = predict_external_rul(frames["telemetry"], req.model_id, _external_model_root())
        audit().append("external_rul_prediction", actor, {"dataset_id": req.dataset_id, "model_id": req.model_id, "asset_count": len(result)})
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc




@app.get("/api/belief-maint/reference")
def belief_maint_reference():
    return build_belief_maint_decision()


@app.post("/api/belief-maint/decision")
def belief_maint_decision(
    req: BeliefMaintRequest,
    actor: str = Depends(require_permissions("run:decision")),
):
    try:
        result = build_belief_maint_decision(
            transition_stress=req.transition_stress,
            crew_capacity=req.crew_capacity,
            opportunity_cost_scale=req.opportunity_cost_scale,
        )
        audit().append("belief_maint_decision", actor, result, subject=result["decision_id"])
        result["approval_status"] = {
            "status": "PENDING_HUMAN_REVIEW",
            "approval_endpoint": "/api/approvals/decision",
        }
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/approvals/decision")
def approve_decision(
    req: DecisionApprovalRequest,
    principal=Depends(require_principal("approver")),  # noqa: B008
):
    if not audit().decision_exists(req.decision_id):
        raise HTTPException(status_code=404, detail="Decision not found in the audit trail")
    try:
        approval = audit().record_approval(
            req.decision_id,
            req.action,
            principal.subject,
            principal.role,
            req.rationale,
        )
        return {
            "approval": approval,
            "execution_boundary": "Approval records review only; they never execute a work order.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/approvals/decision/{decision_id}")
def decision_approvals(
    decision_id: str,
    actor: str = Depends(require_permissions("read:audit")),
):
    if not audit().decision_exists(decision_id):
        raise HTTPException(status_code=404, detail="Decision not found in the audit trail")
    return {"decision_id": decision_id, "approvals": audit().list_approvals(decision_id)}


frontend = Path(__file__).resolve().parents[1] / "frontend"
if frontend.exists():
    app.mount("/static", StaticFiles(directory=frontend), name="static")

    @app.get("/")
    def index():
        return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-store, max-age=0"})

    @app.get("/methodology")
    def methodology():
        return FileResponse(frontend / "methodology.html", headers={"Cache-Control": "no-store, max-age=0"})

    @app.get("/data-gateway")
    def data_gateway_page():
        return FileResponse(frontend / "data_gateway.html", headers={"Cache-Control": "no-store, max-age=0"})

    @app.get("/stress-lab")
    def stress_lab_page():
        return FileResponse(frontend / "stress_lab.html", headers={"Cache-Control": "no-store, max-age=0"})

    @app.get("/operations")
    def operations_page():
        return FileResponse(frontend / "operations.html", headers={"Cache-Control": "no-store, max-age=0"})


    @app.get("/belief-maint")
    def belief_maint_page():
        return FileResponse(frontend / "belief_maint.html", headers={"Cache-Control": "no-store, max-age=0"})
