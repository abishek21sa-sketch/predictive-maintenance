"""Validate that a release ZIP is safe and usable after clean extraction.

This is a packaging gate, not a production certification.  It verifies ZIP
integrity, safe member paths, exclusion boundaries, required release files,
and a real extraction into a temporary directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT_NAME = "Predictive_Maintenance_Intelligence_Platform"
REQUIRED_MEMBERS = (
    f"{ROOT_NAME}/README.md",
    f"{ROOT_NAME}/RELEASE_MANIFEST.json",
    f"{ROOT_NAME}/pyproject.toml",
    f"{ROOT_NAME}/Dockerfile",
    f"{ROOT_NAME}/docker-compose.managed.yml",
    f"{ROOT_NAME}/src/pdm_intelligence/api/main.py",
    f"{ROOT_NAME}/scripts/clean_extract_validate.py",
    f"{ROOT_NAME}/scripts/managed_state_migrate.py",
    f"{ROOT_NAME}/scripts/validate_production_manifest.py",
    f"{ROOT_NAME}/scripts/field_qualification_evidence.py",
    f"{ROOT_NAME}/config/field_qualification_evidence.template.json",
    f"{ROOT_NAME}/src/pdm_intelligence/security/field_validation_evidence.py",
    f"{ROOT_NAME}/deploy/kubernetes/pdm-platform.json",
    f"{ROOT_NAME}/docs/ENTERPRISE_ACCEPTANCE.md",
)
FORBIDDEN_SUFFIXES = {".zip", ".db", ".lic", ".pem", ".key"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _member_path_error(name: str) -> str | None:
    if not name or "\\" in name:
        return "member path must be non-empty and use forward slashes"
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        return "member path is absolute or contains parent traversal"
    if path.parts[0] != ROOT_NAME:
        return f"member is outside the {ROOT_NAME} root"
    return None


def validate_release_archive(archive: Path) -> dict[str, object]:
    """Return a bounded, machine-readable clean-extraction validation report."""

    archive = archive.resolve()
    errors: list[str] = []
    report: dict[str, object] = {
        "status": "FAIL",
        "archive": str(archive),
        "archive_sha256": None,
        "member_count": 0,
        "required_members": list(REQUIRED_MEMBERS),
        "extracted_required_members": [],
        "errors": errors,
        "claim_boundary": (
            "This gate verifies release ZIP integrity, package boundaries, and clean extraction only. "
            "It does not certify infrastructure, security, availability, model performance, or field safety."
        ),
    }
    if not archive.is_file():
        errors.append("archive_not_found")
        return report
    report["archive_sha256"] = _sha256(archive)

    try:
        with zipfile.ZipFile(archive) as zf:
            try:
                bad_member = zf.testzip()
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                errors.append(f"zip_integrity_check_failed:{type(exc).__name__}")
                bad_member = None
            if bad_member:
                errors.append(f"crc_check_failed:{bad_member}")

            infos = zf.infolist()
            report["member_count"] = len(infos)
            names: list[str] = []
            for info in infos:
                name = info.filename
                names.append(name)
                path_error = _member_path_error(name)
                if path_error:
                    errors.append(f"unsafe_member:{name}:{path_error}")
                    continue
                if name in names[:-1]:
                    errors.append(f"duplicate_member:{name}")
                if not info.is_dir() and Path(name).suffix.lower() in FORBIDDEN_SUFFIXES:
                    errors.append(f"forbidden_member:{name}")
                if f"{ROOT_NAME}/data/external/" in name.replace("\\", "/"):
                    errors.append(f"raw_external_data_member:{name}")
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(unix_mode):
                    errors.append(f"symlink_member:{name}")

            missing = sorted(set(REQUIRED_MEMBERS) - set(names))
            errors.extend(f"missing_required_member:{name}" for name in missing)

            if not errors:
                with tempfile.TemporaryDirectory(prefix="pdm-clean-extract-") as td:
                    extraction_root = Path(td) / ROOT_NAME
                    extraction_root.mkdir(parents=True)
                    for info in infos:
                        target = (Path(td) / info.filename).resolve()
                        if not target.is_relative_to(Path(td).resolve()):
                            errors.append(f"extraction_target_escape:{info.filename}")
                            continue
                        zf.extract(info, Path(td))
                    extracted = [
                        name
                        for name in REQUIRED_MEMBERS
                        if (Path(td) / name).is_file()
                    ]
                    report["extracted_required_members"] = extracted
                    errors.extend(
                        f"extraction_missing_required_member:{name}"
                        for name in set(REQUIRED_MEMBERS) - set(extracted)
                    )
                    manifest = Path(td) / f"{ROOT_NAME}/RELEASE_MANIFEST.json"
                    try:
                        json.loads(manifest.read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                        errors.append(f"manifest_not_valid_json:{type(exc).__name__}")
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"archive_open_failed:{type(exc).__name__}")

    if not errors:
        report["status"] = "PASS"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a release ZIP after clean extraction")
    parser.add_argument(
        "--archive",
        default="dist/Predictive_Maintenance_Intelligence_Final.zip",
        help="release ZIP to validate",
    )
    parser.add_argument(
        "--output",
        default="artifacts/clean_extract_validation.json",
        help="JSON report path",
    )
    args = parser.parse_args()
    report = validate_release_archive(Path(args.archive))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
