"""Validate the vendor-neutral Kubernetes production topology template.

The template intentionally contains replacement markers for site-specific
identity, database, ingress, observability, recovery, connector, and change
records.  This validator checks the secure topology and fail-closed wiring; it
does not claim that those external systems exist.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "deploy" / "kubernetes" / "pdm-platform.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "production_topology_template.json"
REQUIRED_KINDS = {
    "Namespace",
    "ServiceAccount",
    "Job",
    "Deployment",
    "Service",
    "Ingress",
    "PodDisruptionBudget",
    "NetworkPolicy",
}
REQUIRED_CONFIG = {
    "PDM_AUTH_MODE": "production",
    "PDM_STATE_BACKEND": "managed",
    "PDM_MANAGED_STATE_ADAPTER": "sqlalchemy",
    "PDM_READINESS_PROBE_DATABASE": "1",
    "PDM_REQUIRE_APPROVED_MODELS": "1",
    "PDM_OBSERVABILITY_ENABLED": "1",
}


def _containers(deployment: dict[str, Any]) -> list[dict[str, Any]]:
    return deployment.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])


def validate_manifest(path: Path, *, require_resolved: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    replacements: set[str] = set()
    report: dict[str, Any] = {
        "status": "FAIL",
        "manifest": str(path.resolve()),
        "errors": errors,
        "external_replacements_required": [],
        "claim_boundary": (
            "This validates Kubernetes topology and fail-closed wiring only. It does not certify "
            "a cluster, secret manager, managed database, TLS ingress, observability, recovery, "
            "connector, change approval, model performance, or field safety."
        ),
    }
    if not path.is_file():
        errors.append("manifest_not_found")
        return report
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"manifest_unreadable_or_invalid_json:{type(exc).__name__}")
        return report
    if document.get("kind") != "List" or not isinstance(document.get("items"), list):
        errors.append("manifest_must_be_kubernetes_list")
        return report
    resources = [item for item in document["items"] if isinstance(item, dict)]
    by_kind = {item.get("kind"): item for item in resources}
    missing_kinds = sorted(REQUIRED_KINDS - set(by_kind))
    errors.extend(f"missing_kind:{kind}" for kind in missing_kinds)
    resource_keys = [
        (
            item.get("apiVersion"),
            item.get("kind"),
            item.get("metadata", {}).get("namespace"),
            item.get("metadata", {}).get("name"),
        )
        for item in resources
    ]
    if len(set(resource_keys)) != len(resource_keys):
        errors.append("duplicate_resource_identity")

    configmaps = [item for item in resources if item.get("kind") == "ConfigMap"]
    platform_config = next(
        (item.get("data", {}) for item in configmaps if "PDM_AUTH_MODE" in item.get("data", {})),
        {},
    )
    for name, expected in REQUIRED_CONFIG.items():
        if platform_config.get(name) != expected:
            errors.append(f"config_mismatch:{name}")
    if platform_config.get("PDM_FIELD_QUALIFICATION_EVIDENCE_FILE") != "/etc/pdm/field/qualification.json":
        errors.append("config_mismatch:PDM_FIELD_QUALIFICATION_EVIDENCE_FILE")
    field_evidence_config = next(
        (
            item
            for item in configmaps
            if item.get("metadata", {}).get("name") == "pdm-field-qualification-evidence"
        ),
        {},
    )
    if field_evidence_config.get("data", {}).get("qualification.json") != "{}\n":
        errors.append("field_qualification_evidence_configmap_missing")

    namespace = by_kind.get("Namespace", {})
    if namespace.get("metadata", {}).get("name") != "pdm-production":
        errors.append("production_namespace_missing")
    namespace_labels = namespace.get("metadata", {}).get("labels", {})
    if namespace_labels.get("pod-security.kubernetes.io/enforce") != "restricted":
        errors.append("restricted_pod_security_not_enforced")

    service_account = by_kind.get("ServiceAccount", {})
    if service_account.get("automountServiceAccountToken") is not False:
        errors.append("service_account_token_automount_not_disabled")

    migration = by_kind.get("Job", {})
    migration_spec = migration.get("spec", {})
    migration_pod = migration_spec.get("template", {}).get("spec", {})
    migration_containers = migration_pod.get("containers", [])
    if migration_spec.get("suspend") is not True:
        errors.append("managed_migration_job_must_be_suspended_by_default")
    if migration_pod.get("restartPolicy") != "Never":
        errors.append("managed_migration_job_restart_policy_not_never")
    if len(migration_containers) != 1:
        errors.append("managed_migration_job_container_missing")
    else:
        migration_container = migration_containers[0]
        command = migration_container.get("command", [])
        if "scripts/managed_state_migrate.py" not in command:
            errors.append("managed_migration_command_missing")
        secret_names = {
            item.get("name")
            for item in migration_container.get("env", [])
            if isinstance(item, dict)
        }
        if "PDM_DATABASE_URL" not in secret_names:
            errors.append("managed_migration_database_secret_missing")

    deployment = by_kind.get("Deployment", {})
    spec = deployment.get("spec", {})
    if spec.get("replicas", 0) < 3:
        errors.append("deployment_replica_floor_below_three")
    rolling = spec.get("strategy", {}).get("rollingUpdate", {})
    if rolling.get("maxUnavailable") != 0 or rolling.get("maxSurge") != 1:
        errors.append("rolling_update_policy_not_zero_unavailable_one_surge")
    pod = spec.get("template", {}).get("spec", {})
    if pod.get("automountServiceAccountToken") is not False:
        errors.append("pod_service_account_token_automount_not_disabled")
    security = pod.get("securityContext", {})
    if security.get("runAsNonRoot") is not True or security.get("seccompProfile", {}).get("type") != "RuntimeDefault":
        errors.append("pod_security_context_not_restricted")
    if len(_containers(deployment)) != 1:
        errors.append("deployment_must_have_one_platform_container")
    else:
        container = _containers(deployment)[0]
        image = str(container.get("image", ""))
        if "@sha256:" not in image:
            errors.append("container_image_not_digest_pinned")
        container_security = container.get("securityContext", {})
        if container_security.get("allowPrivilegeEscalation") is not False:
            errors.append("privilege_escalation_not_disabled")
        if container_security.get("readOnlyRootFilesystem") is not True:
            errors.append("root_filesystem_not_read_only")
        if "ALL" not in container_security.get("capabilities", {}).get("drop", []):
            errors.append("linux_capabilities_not_dropped")
        resources = container.get("resources", {})
        if not resources.get("requests") or not resources.get("limits"):
            errors.append("container_resources_not_bounded")
        env_names = {
            item.get("name")
            for item in container.get("env", [])
            if isinstance(item, dict)
        }
        for name in ("PDM_API_KEYS", "PDM_DATABASE_URL"):
            if name not in env_names:
                errors.append(f"secret_ref_missing:{name}")
        probes = {
            name: container.get(name, {}).get("httpGet", {})
            for name in ("startupProbe", "readinessProbe", "livenessProbe")
        }
        if probes["readinessProbe"].get("path") != "/api/health/ready":
            errors.append("readiness_probe_not_production_readiness")
        for name in ("startupProbe", "livenessProbe"):
            if probes[name].get("path") != "/api/health":
                errors.append(f"{name}_not_health_probe")
        volume_mounts = {
            item.get("name"): item
            for item in container.get("volumeMounts", [])
            if isinstance(item, dict)
        }
        field_mount = volume_mounts.get("field-qualification-evidence", {})
        if field_mount.get("mountPath") != "/etc/pdm/field" or field_mount.get("readOnly") is not True:
            errors.append("field_qualification_evidence_mount_missing")

    ingress = by_kind.get("Ingress", {})
    ingress_spec = ingress.get("spec", {})
    if not ingress_spec.get("tls") or not ingress_spec.get("rules"):
        errors.append("tls_ingress_boundary_missing")
    service = by_kind.get("Service", {}).get("spec", {})
    if service.get("type") != "ClusterIP":
        errors.append("service_not_cluster_internal")
    pdb = by_kind.get("PodDisruptionBudget", {}).get("spec", {})
    if pdb.get("minAvailable") != 2:
        errors.append("pod_disruption_budget_floor_missing")
    network_policy = by_kind.get("NetworkPolicy", {}).get("spec", {})
    if set(network_policy.get("policyTypes", [])) != {"Ingress", "Egress"}:
        errors.append("network_policy_not_bounded_for_ingress_and_egress")

    serialized = json.dumps(document, sort_keys=True)
    for marker in sorted({part for part in serialized.split('"') if "REPLACE_WITH_" in part}):
        replacements.add(marker)
    report["external_replacements_required"] = sorted(replacements)
    if require_resolved and replacements:
        errors.append("unresolved_external_replacements")
    if not errors:
        report["status"] = "PASS_TEMPLATE"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the production Kubernetes topology template")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when site-specific replacement markers remain",
    )
    args = parser.parse_args()
    report = validate_manifest(Path(args.manifest), require_resolved=args.strict)
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS_TEMPLATE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
