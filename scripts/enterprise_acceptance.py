"""Run the bounded enterprise-readiness acceptance lanes.

This command verifies the reference implementation and produces a non-secret
report. It deliberately reports site-specific deployment work as
``BLOCKED_EXTERNAL`` instead of turning configuration declarations into a
production certification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "artifacts" / "enterprise_acceptance.json"
TEST_TIMEOUT_SECONDS = 900


def _result(status: str, summary: str, **evidence: Any) -> dict[str, Any]:
    return {"status": status, "summary": summary, **evidence}


def _production_lane() -> dict[str, Any]:
    from pdm_intelligence.security.production_readiness import production_readiness

    report = production_readiness()
    checks = report["checks"]
    return _result(
        "PASS" if not report["blockers"] else "BLOCKED_EXTERNAL",
        "All deployment declarations are present" if not report["blockers"] else "External deployment evidence is required",
        blockers=report["blockers"],
        checks=checks,
        deployment_evidence=report["deployment_evidence"],
        evidence_class=report["evidence_class"],
    )


def _field_qualification_lane() -> dict[str, Any]:
    """Report prospective field qualification separately from deployment wiring."""

    from pdm_intelligence.security.production_readiness import production_readiness

    report = production_readiness()
    check = report["checks"]["prospective_field_validation"]
    passed = check["ready"] is True
    return _result(
        "PASS" if passed else "BLOCKED_EXTERNAL",
        "Release-bound prospective field qualification evidence is present"
        if passed
        else "Prospective field qualification requires site-owned evidence with completed follow-up",
        evidence_class="PROSPECTIVE_FIELD_QUALIFICATION_ATTESTATION_NOT_CERTIFICATION",
        check=check,
        evidence=report["field_qualification_evidence"],
    )


def _model_quality_lane() -> dict[str, Any]:
    path = ROOT / "artifacts" / "phase5_diagnostics.json"
    if not path.exists():
        return _result(
            "BLOCKED_EXTERNAL",
            "Run the full MetroPT-3 preparation and Phase 5 diagnostics first",
            evidence_class="REAL_OPERATIONAL_DATA_REQUIRED",
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    from pdm_intelligence.real_data.metropt import load_metropt_runtime

    runtime_root = ROOT / "data" / "external" / "metropt3"
    runtime_path = runtime_root / "derived" / "metropt_runtime.json"
    runtime = load_metropt_runtime(runtime_root)
    if runtime is None:
        return _result(
            "BLOCKED_EXTERNAL",
            "The trusted MetroPT runtime evidence is missing",
            evidence_class="REAL_OPERATIONAL_DATA_REQUIRED",
            runtime_evidence_sha256=None,
            diagnostics_runtime_evidence_sha256=payload.get("runtime_evidence_sha256"),
        )
    runtime_integrity = runtime.get("integrity_check", {})
    if runtime_integrity.get("passed") is not True:
        return _result(
            "FAIL",
            "The trusted MetroPT runtime evidence failed integrity verification",
            evidence_class="REAL_OPERATIONAL_DATA_INTEGRITY_REQUIRED",
            runtime_integrity=runtime_integrity,
        )
    runtime_evidence_sha256 = hashlib.sha256(runtime_path.read_bytes()).hexdigest()
    diagnostics_runtime_evidence_sha256 = payload.get("runtime_evidence_sha256")
    if diagnostics_runtime_evidence_sha256 != runtime_evidence_sha256:
        return _result(
            "FAIL",
            "The model-quality diagnostics are stale or detached from the trusted runtime evidence",
            evidence_class="REAL_OPERATIONAL_DATA_EVIDENCE_BINDING_REQUIRED",
            runtime_evidence_sha256=runtime_evidence_sha256,
            diagnostics_runtime_evidence_sha256=diagnostics_runtime_evidence_sha256,
            errors=["diagnostics_runtime_evidence_sha256_mismatch"],
        )
    checks = payload.get("checks", {})
    metrics = payload.get("model", {}).get("holdout_metrics", {})
    anomaly_quality = payload.get("model", {}).get("anomaly_quality", {})
    anomaly_holdout = anomaly_quality.get("holdout", {}) if isinstance(anomaly_quality, dict) else {}
    quality_gate = payload.get("model", {}).get("quality_gate", {})
    runtime_quality_gate = runtime.get("model", {}).get("quality_gate", {})
    quality_gate_matches_runtime = quality_gate == runtime_quality_gate
    passed = bool(
        checks.get("validation_model_quality_gate", checks.get("validation_beats_prevalence"))
        and checks.get("holdout_model_quality_gate", checks.get("holdout_beats_prevalence"))
        and quality_gate.get("passed")
        and quality_gate.get("promotion_allowed") is True
        and quality_gate_matches_runtime
        and runtime_quality_gate.get("passed") is True
        and runtime_quality_gate.get("promotion_allowed") is True
        and runtime.get("model_status") == "PROMOTION_READY"
    )
    return _result(
        "PASS" if passed else "BLOCKED_EXTERNAL",
        "Chronological validation and future holdout average precision clear their prevalence baselines with independent event coverage"
        if passed
        else "Real-data model-quality gate is not passed; improve validation, future holdout and independent event coverage without weakening the gate",
        evidence_class="REAL_OPERATIONAL_DATA_END_TO_END_VALIDATION",
        holdout_average_precision=metrics.get("average_precision"),
        holdout_prevalence=metrics.get("prevalence"),
        holdout_roc_auc=metrics.get("roc_auc"),
        anomaly_holdout_average_precision=anomaly_holdout.get("average_precision"),
        anomaly_holdout_roc_auc=anomaly_holdout.get("roc_auc"),
        quality_gate=quality_gate,
        runtime_evidence_sha256=runtime_evidence_sha256,
        diagnostics_runtime_evidence_sha256=diagnostics_runtime_evidence_sha256,
        quality_gate_matches_runtime=quality_gate_matches_runtime,
        runtime_model_status=runtime.get("model_status"),
        gate=passed,
    )


def _backup_restore_lane() -> dict[str, Any]:
    """Exercise a local backup/restore round trip without touching user data."""

    with tempfile.TemporaryDirectory(prefix="pdm-backup-restore-") as raw_dir:
        root = Path(raw_dir)
        source = root / "source.db"
        restored = root / "restored.db"
        with closing(sqlite3.connect(source)) as con:
            con.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            con.execute("INSERT INTO evidence(value) VALUES (?)", ("round-trip",))
            con.commit()
            with closing(sqlite3.connect(restored)) as destination:
                con.backup(destination)
        with closing(sqlite3.connect(restored)) as con:
            value = con.execute("SELECT value FROM evidence WHERE id=1").fetchone()
        if value != ("round-trip",):
            return _result("FAIL", "Local SQLite restore verification failed")
    return _result(
        "PASS_REFERENCE_ONLY",
        "Local SQLite backup/restore round trip passed",
        evidence_class="REFERENCE_LOCAL_BACKUP_RESTORE_NOT_DR_EVIDENCE",
    )


def _cmms_lane() -> dict[str, Any]:
    from pdm_intelligence.governance.audit import AuditStore
    from pdm_intelligence.integrations.cmms import reconcile_work_orders

    record = {
        "work_order_id": "ACCEPTANCE-WO-001",
        "asset_id": "APU-001",
        "status": "OPEN",
        "planned_start": "2026-01-01T00:00:00Z",
        "duration_hours": 4,
        "required_skill": "mechanic",
        "part_id": "P-001",
        "part_qty": 1,
        "action": "inspect",
        "external_revision": 1,
        "source_updated_at": "2026-01-01T00:00:00Z",
    }
    with tempfile.TemporaryDirectory(prefix="pdm-cmms-acceptance-") as raw_dir:
        db = Path(raw_dir) / "cmms.db"
        audit = AuditStore(db)
        result = reconcile_work_orders(
            [record], db, source_system="acceptance", actor="acceptance", audit_store=audit
        )
        verified = audit.verify_chain()
    passed = result["status"] == "APPLIED" and verified["valid"] and result.get("audit_event_id")
    return _result(
        "PASS_REFERENCE_ONLY" if passed else "FAIL",
        "CMMS contract, idempotent mirror, and audit linkage passed"
        if passed
        else "CMMS reference acceptance failed",
        evidence_class="REFERENCE_CMMS_EXCHANGE_NOT_SITE_CONNECTOR_EVIDENCE",
        reconciliation_status=result["status"],
        audit_chain_valid=verified["valid"],
    )


def _observability_lane() -> dict[str, Any]:
    module = ROOT / "src" / "pdm_intelligence" / "monitoring" / "runtime.py"
    api = ROOT / "src" / "pdm_intelligence" / "api" / "main.py"
    load_smoke = ROOT / "scripts" / "load_smoke.py"
    drift = ROOT / "src" / "pdm_intelligence" / "monitoring" / "drift.py"
    passed = (
        module.exists()
        and api.exists()
        and load_smoke.exists()
        and drift.exists()
        and "runtime_metrics" in api.read_text(encoding="utf-8")
    )
    return _result(
        "PASS_REFERENCE_ONLY" if passed else "FAIL",
        "Request correlation and authenticated runtime metrics are implemented"
        if passed
        else "Runtime observability implementation is missing",
        evidence_class="REFERENCE_RUNTIME_INSTRUMENTATION_NOT_MANAGED_OBSERVABILITY_EVIDENCE",
    )


def _model_governance_lane() -> dict[str, Any]:
    module = ROOT / "src" / "pdm_intelligence" / "models" / "model_registry.py"
    lifecycle = ROOT / "src" / "pdm_intelligence" / "external" / "model_lifecycle.py"
    api = ROOT / "src" / "pdm_intelligence" / "api" / "main.py"
    passed = module.exists() and lifecycle.exists() and "ModelRegistry" in lifecycle.read_text(encoding="utf-8") and "/api/models/registry" in api.read_text(encoding="utf-8")
    return _result(
        "PASS_REFERENCE_ONLY" if passed else "FAIL",
        "Hash-registered candidate models and human promotion gate are implemented"
        if passed
        else "Model-governance implementation is missing",
        evidence_class="REFERENCE_MODEL_GOVERNANCE_NOT_ENTERPRISE_REGISTRY_CERTIFICATION",
    )


def _change_control_lane() -> dict[str, Any]:
    artifact = ROOT / "dist" / "Predictive_Maintenance_Intelligence_Final.zip"
    manifest = ROOT / "RELEASE_MANIFEST.json"
    if not artifact.exists() or not manifest.exists():
        return _result("FAIL", "Release artifact or manifest is missing")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    with zipfile.ZipFile(artifact) as archive:
        names = set(archive.namelist())
    passed = (
        not any(name.lower().endswith(".zip") for name in names)
        and any(name.endswith("monitoring/runtime.py") for name in names)
        and any(name.endswith("scripts/enterprise_acceptance.py") for name in names)
        and any(name.endswith("docs/ENTERPRISE_ACCEPTANCE.md") for name in names)
        and "release" in json.loads(manifest.read_text(encoding="utf-8"))
    )
    return _result(
        "PASS_REFERENCE_ONLY" if passed else "FAIL",
        "Clean release artifact and manifest are present" if passed else "Release artifact validation failed",
        evidence_class="REFERENCE_RELEASE_INTEGRITY_NOT_APPROVED_CHANGE_EVIDENCE",
        artifact_sha256=digest,
        nested_archives=sorted(name for name in names if name.lower().endswith(".zip")),
    )


def _clean_extraction_lane() -> dict[str, Any]:
    try:
        from scripts.clean_extract_validate import validate_release_archive
    except ModuleNotFoundError:
        from clean_extract_validate import validate_release_archive

    artifact = ROOT / "dist" / "Predictive_Maintenance_Intelligence_Final.zip"
    report = validate_release_archive(artifact)
    passed = report["status"] == "PASS"
    return _result(
        "PASS_REFERENCE_ONLY" if passed else "FAIL",
        "Release ZIP passed clean-extraction validation"
        if passed
        else "Release ZIP failed clean-extraction validation",
        evidence_class="REFERENCE_RELEASE_EXTRACTION_NOT_PRODUCTION_CERTIFICATION",
        archive_sha256=report.get("archive_sha256"),
        member_count=report.get("member_count"),
        required_members=report.get("required_members"),
        errors=report.get("errors", []),
    )


def _production_topology_lane() -> dict[str, Any]:
    try:
        from scripts.validate_production_manifest import DEFAULT_MANIFEST, validate_manifest
    except ModuleNotFoundError:
        from validate_production_manifest import DEFAULT_MANIFEST, validate_manifest

    report = validate_manifest(DEFAULT_MANIFEST)
    passed = report["status"] == "PASS_TEMPLATE"
    return _result(
        "PASS_REFERENCE_ONLY" if passed else "FAIL",
        "Production Kubernetes topology template passed static security validation"
        if passed
        else "Production Kubernetes topology template failed static security validation",
        evidence_class="REFERENCE_PRODUCTION_TOPOLOGY_NOT_CLUSTER_CERTIFICATION",
        errors=report.get("errors", []),
        external_replacements_required=report.get("external_replacements_required", []),
    )


def _run_tests(full: bool) -> dict[str, Any]:
    targets = [] if full else [
        "tests/test_observability.py",
        "tests/test_production_readiness.py",
        "tests/test_cmms_integration.py",
        "tests/test_enterprise_hardening.py",
    ]
    command = [sys.executable, "-m", "pytest", "-q"]
    if full:
        command.extend(["-W", "error"])
    command.extend(targets)
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=TEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        output: list[str] = []
        for value in (exc.stdout, exc.stderr):
            if not value:
                continue
            if isinstance(value, bytes):
                value = value.decode(errors="replace")
            output.extend(str(value).strip().splitlines())
        return _result(
            "FAIL",
            f"Automated acceptance tests exceeded the {TEST_TIMEOUT_SECONDS}-second timeout",
            return_code=None,
            command=command,
            output_tail=output[-20:],
        )
    output = (completed.stdout + completed.stderr).strip().splitlines()
    return _result(
        "PASS" if completed.returncode == 0 else "FAIL",
        "Automated acceptance tests passed" if completed.returncode == 0 else "Automated acceptance tests failed",
        return_code=completed.returncode,
        command=command,
        output_tail=output[-20:],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run enterprise-readiness acceptance lanes")
    parser.add_argument("--full", action="store_true", help="Run the complete warning-clean test suite")
    parser.add_argument("--run-tests", action="store_true", help="Run focused tests; otherwise only run acceptance lanes")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero when external production blockers remain")
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()

    lanes = {
        "model_quality": _model_quality_lane(),
        "deployment_controls": _production_lane(),
        "prospective_field_qualification": _field_qualification_lane(),
        "backup_restore": _backup_restore_lane(),
        "cmms_erp_connector": _cmms_lane(),
        "release_change_control": _change_control_lane(),
        "clean_extraction": _clean_extraction_lane(),
        "production_topology": _production_topology_lane(),
        "runtime_observability": _observability_lane(),
        "model_governance": _model_governance_lane(),
    }
    if args.run_tests:
        lanes["automated_tests"] = _run_tests(args.full)
    failures = [name for name, item in lanes.items() if item["status"] == "FAIL"]
    blockers = [name for name, item in lanes.items() if item["status"] == "BLOCKED_EXTERNAL"]
    report = {
        "release": json.loads((ROOT / "RELEASE_MANIFEST.json").read_text(encoding="utf-8")).get("release"),
        "overall": "PASS" if not failures and not blockers else "NOT_READY",
        "lanes": lanes,
        "failures": failures,
        "external_blockers": blockers,
        "claim_boundary": (
            "Reference acceptance and deployment declarations are not a production certification. "
            "Site infrastructure, identity, connector, recovery, and prospective field evidence remain external."
        ),
    }
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if failures:
        return 1
    return 2 if args.strict and blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
