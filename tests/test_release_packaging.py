import zipfile
from pathlib import Path

from scripts.clean_extract_validate import validate_release_archive
from scripts.package_release import ROOT_NAME, package_repository


def test_managed_compose_requires_explicit_migration_profile():
    repo = Path(__file__).resolve().parents[1]
    compose = (repo / "docker-compose.managed.yml").read_text(encoding="utf-8")

    assert "migrate:" in compose
    assert 'profiles: ["migration"]' in compose
    assert "scripts/managed_state_migrate.py" in compose
    assert "PDM_STATE_BACKEND: managed" in compose
    assert "platform:" in compose


def test_release_packager_keeps_external_code_but_excludes_external_raw_data(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src/pdm_intelligence/external").mkdir(parents=True)
    (repo / "src/pdm_intelligence/external/gateway.py").write_text("VALUE=1\n", encoding="utf-8")
    (repo / "data/external/metropt3/raw").mkdir(parents=True)
    (repo / "data/external/metropt3/raw/source.csv").write_text("secret-ish raw data", encoding="utf-8")
    (repo / "README.md").write_text("release", encoding="utf-8")
    out = tmp_path / "release.zip"
    package_repository(repo, out)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert f"{ROOT_NAME}/src/pdm_intelligence/external/gateway.py" in names
    assert not any("/data/external/" in name for name in names)
    assert f"{ROOT_NAME}/README.md" in names


def test_release_packager_excludes_output_directory_and_nested_archives(tmp_path):
    repo = tmp_path / "repo"
    (repo / "dist").mkdir(parents=True)
    (repo / "dist" / "previous-release.zip").write_bytes(b"old release")
    (repo / "nested.zip").write_bytes(b"nested archive")
    out = tmp_path / "release.zip"
    package_repository(repo, out)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert not any(name.endswith(".zip") for name in names)


def _write_required_release_tree(repo: Path) -> None:
    required = (
        "README.md",
        "RELEASE_MANIFEST.json",
        "pyproject.toml",
        "Dockerfile",
        "docker-compose.managed.yml",
        "src/pdm_intelligence/api/main.py",
        "scripts/clean_extract_validate.py",
        "scripts/managed_state_migrate.py",
        "scripts/validate_production_manifest.py",
        "scripts/field_qualification_evidence.py",
        "config/field_qualification_evidence.template.json",
        "src/pdm_intelligence/security/field_validation_evidence.py",
        "deploy/kubernetes/pdm-platform.json",
        "docs/ENTERPRISE_ACCEPTANCE.md",
    )
    for relative in required:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n" if target.name.endswith(".json") else "release\n", encoding="utf-8")


def test_clean_extract_validator_extracts_and_checks_required_release_files(tmp_path):
    repo = tmp_path / "repo"
    _write_required_release_tree(repo)
    out = tmp_path / "release.zip"
    package_repository(repo, out)

    report = validate_release_archive(out)

    assert report["status"] == "PASS"
    assert report["member_count"] == 14
    assert report["errors"] == []


def test_clean_extract_validator_rejects_traversal_and_nested_archives(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{ROOT_NAME}/README.md", "release")
        zf.writestr(f"{ROOT_NAME}/../escape.txt", "escape")
        zf.writestr(f"{ROOT_NAME}/nested.zip", "not a release")

    report = validate_release_archive(archive)

    assert report["status"] == "FAIL"
    assert any(error.startswith("unsafe_member:") for error in report["errors"])
    assert any(error.startswith("forbidden_member:") for error in report["errors"])
