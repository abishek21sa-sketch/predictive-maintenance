import json
from pathlib import Path

from scripts.validate_production_manifest import validate_manifest


def test_production_topology_template_passes_static_security_gate():
    repo = Path(__file__).resolve().parents[1]

    report = validate_manifest(repo / "deploy/kubernetes/pdm-platform.json")

    assert report["status"] == "PASS_TEMPLATE"
    assert report["errors"] == []
    assert "REPLACE_WITH_RELEASE_ARTIFACT_SHA256" in report["external_replacements_required"]
    assert "REPLACE_WITH_TRUSTED_TLS_SECRET" in report["external_replacements_required"]


def test_production_topology_validator_rejects_readiness_probe_regression(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    document = json.loads((repo / "deploy/kubernetes/pdm-platform.json").read_text(encoding="utf-8"))
    deployment = next(item for item in document["items"] if item["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    container["readinessProbe"]["httpGet"]["path"] = "/api/health"
    candidate = tmp_path / "tampered.json"
    candidate.write_text(json.dumps(document), encoding="utf-8")

    report = validate_manifest(candidate)

    assert report["status"] == "FAIL"
    assert "readiness_probe_not_production_readiness" in report["errors"]


def test_production_topology_strict_mode_rejects_unresolved_site_markers():
    repo = Path(__file__).resolve().parents[1]

    report = validate_manifest(repo / "deploy/kubernetes/pdm-platform.json", require_resolved=True)

    assert report["status"] == "FAIL"
    assert "unresolved_external_replacements" in report["errors"]
