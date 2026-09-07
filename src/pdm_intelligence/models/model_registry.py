"""Filesystem model registry with explicit promotion and rollback metadata.

The registry is a local reference implementation of the model-governance
boundary. Models are candidates by default; promotion requires a recorded
validation gate plus an explicit human actor and rationale. The JSON index is
updated atomically and every lifecycle transition is recorded as an event.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
REGISTRY_FILE = "registry.json"
PROMOTION_GATE_VERSION = 2


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ModelRegistry:
    """Atomic, human-gated model registry for a model-root directory."""

    def __init__(self, root: str | Path = "data/external_models"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / REGISTRY_FILE
        self.lock_path = self.root / ".registry.lock"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"registry_version": 1, "models": [], "events": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Model registry is unreadable") from exc
        if not isinstance(payload, dict) or payload.get("registry_version") != 1:
            raise RuntimeError("Unsupported model registry format")
        payload.setdefault("models", [])
        payload.setdefault("events", [])
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def _lock(self, timeout_seconds: float = 5.0):
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(descriptor)
                return
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for the model registry lock")
                time.sleep(0.02)

    def _unlock(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    @staticmethod
    def _validate_manifest(model_id: str, manifest: dict[str, Any]) -> None:
        if not MODEL_ID.fullmatch(model_id):
            raise ValueError("model_id contains unsupported characters")
        if manifest.get("model_id") != model_id:
            raise ValueError("Model manifest model_id does not match its directory")
        if not manifest.get("dataset_id") or not manifest.get("features"):
            raise ValueError("Model manifest must identify a dataset and feature contract")
        metrics = manifest.get("metrics", {})
        baseline = manifest.get("baseline_metrics", {})
        if not isinstance(metrics, dict) or not isinstance(baseline, dict):
            raise TypeError("Model manifest metrics are invalid")
        rmse = metrics.get("rmse")
        baseline_rmse = baseline.get("rmse")
        if not isinstance(rmse, (int, float)) or not isinstance(baseline_rmse, (int, float)):
            raise TypeError("Model manifest must contain numeric model and baseline RMSE")

    @staticmethod
    def _promotion_gate_report(manifest: dict[str, Any]) -> dict[str, Any]:
        """Validate explicit model evidence before a candidate can be approved."""

        supplied = manifest.get("promotion_gate")
        errors: list[str] = []
        if not isinstance(supplied, dict):
            return {
                "version": PROMOTION_GATE_VERSION,
                "passed": False,
                "rule": "explicit promotion_gate evidence is required",
                "errors": ["promotion_gate_missing"],
            }
        if supplied.get("version") != PROMOTION_GATE_VERSION:
            errors.append("promotion_gate_version_unsupported")
        if supplied.get("passed") is not True:
            errors.append("promotion_gate_not_passed")
        if not isinstance(supplied.get("rule"), str) or not supplied["rule"].strip():
            errors.append("promotion_gate_rule_missing")
        if not isinstance(supplied.get("validation_design"), str) or not supplied["validation_design"].strip():
            errors.append("promotion_gate_validation_design_missing")
        comparison = supplied.get("metric_comparison")
        if not isinstance(comparison, dict):
            errors.append("promotion_gate_metric_comparison_missing")
        else:
            for field in ("rmse_beats_baseline", "mae_beats_baseline"):
                if comparison.get(field) is not True:
                    errors.append(f"promotion_gate_{field}_not_proven")
            for field in ("selected_rmse", "baseline_rmse", "selected_mae", "baseline_mae"):
                value = comparison.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(f"promotion_gate_{field}_missing_or_invalid")
        if not isinstance(supplied.get("validation_asset_count"), int) or supplied["validation_asset_count"] < 2:
            errors.append("promotion_gate_validation_assets_insufficient")
        if supplied.get("asset_disjoint") is not True:
            errors.append("promotion_gate_asset_overlap")
        if supplied.get("target_excluded_from_features") is not True:
            errors.append("promotion_gate_target_leakage_unproven")
        report = dict(supplied)
        report["version"] = supplied.get("version", PROMOTION_GATE_VERSION)
        report["passed"] = not errors
        if errors:
            report["errors"] = sorted({*supplied.get("errors", []), *errors})
        return report

    def register(self, model_id: str, *, actor: str = "system", reason: str = "training_complete") -> dict[str, Any]:
        """Register a validated model as a candidate, idempotently."""

        model_dir = self.root / model_id
        manifest_path = model_dir / "manifest.json"
        artifact_path = model_dir / "model.joblib"
        if not manifest_path.is_file() or not artifact_path.is_file():
            raise ValueError("Model directory must contain manifest.json and model.joblib")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._validate_manifest(model_id, manifest)
        promotion_gate = self._promotion_gate_report(manifest)
        entry = {
            "model_id": model_id,
            "dataset_id": str(manifest["dataset_id"]),
            "stage": "candidate",
            "registered_at": _now(),
            "registered_by": actor,
            "registration_reason": reason,
            "artifact_sha256": _sha256(artifact_path),
            "manifest_sha256": _sha256(manifest_path),
            "selected_model": manifest.get("selected_model", "unknown"),
            "metrics": manifest.get("metrics", {}),
            "baseline_metrics": manifest.get("baseline_metrics", {}),
            "promotion_gate": {
                **promotion_gate,
            },
            "evidence_class": manifest.get("evidence_class", "UNCLASSIFIED_MODEL_EVIDENCE"),
        }
        self._lock()
        try:
            payload = self._read()
            existing = next((item for item in payload["models"] if item["model_id"] == model_id), None)
            if existing:
                if existing["artifact_sha256"] != entry["artifact_sha256"]:
                    raise ValueError("Model ID already exists with different artifact content")
                return existing
            payload["models"].append(entry)
            payload["events"].append({"event": "registered", "model_id": model_id, "actor": actor, "reason": reason, "at": _now()})
            self._write(payload)
            return entry
        finally:
            self._unlock()

    def list(self, *, dataset_id: str | None = None, stage: str | None = None) -> list[dict[str, Any]]:
        items = self._read()["models"]
        if dataset_id is not None:
            items = [item for item in items if item["dataset_id"] == dataset_id]
        if stage is not None:
            items = [item for item in items if item["stage"] == stage]
        return sorted(items, key=lambda item: item.get("registered_at", ""), reverse=True)

    def get(self, model_id: str) -> dict[str, Any] | None:
        return next((item for item in self._read()["models"] if item["model_id"] == model_id), None)

    def promote(self, model_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        """Promote a candidate only when its recorded validation gate passed."""

        if not actor.strip() or not reason.strip():
            raise ValueError("Promotion requires a non-empty actor and rationale")
        self._lock()
        try:
            payload = self._read()
            target = next((item for item in payload["models"] if item["model_id"] == model_id), None)
            if target is None:
                raise ValueError(f"Unknown model {model_id}")
            if target["stage"] == "approved":
                return target
            if target["stage"] != "candidate":
                raise ValueError(f"Only candidate models can be promoted; current stage is {target['stage']}")
            model_dir = self.root / model_id
            manifest_path = model_dir / "manifest.json"
            artifact_path = model_dir / "model.joblib"
            if not manifest_path.is_file() or not artifact_path.is_file():
                raise ValueError("Model artifacts are missing; promotion is blocked")
            if target.get("artifact_sha256") != _sha256(artifact_path) or target.get("manifest_sha256") != _sha256(manifest_path):
                raise ValueError("Model artifact or manifest changed after registration; re-register before promotion")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self._validate_manifest(model_id, manifest)
            current_gate = self._promotion_gate_report(manifest)
            if target.get("promotion_gate") != current_gate or not current_gate.get("passed", False):
                raise ValueError("Model validation gate did not pass; promotion is blocked")
            for item in payload["models"]:
                if item["dataset_id"] == target["dataset_id"] and item["stage"] == "approved":
                    item["stage"] = "retired"
                    item["retired_at"] = _now()
                    item["retired_by"] = actor
            target["stage"] = "approved"
            target["approved_at"] = _now()
            target["approved_by"] = actor
            target["approval_reason"] = reason
            payload["events"].append({"event": "promoted", "model_id": model_id, "actor": actor, "reason": reason, "at": _now()})
            self._write(payload)
            return target
        finally:
            self._unlock()
