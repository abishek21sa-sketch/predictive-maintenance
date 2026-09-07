from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT_NAME = "Predictive_Maintenance_Intelligence_Platform"
EXCLUDED_DIR_NAMES = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "htmlcov", "dist", "dist-ci"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".lic", ".pem", ".key", ".zip"}
EXCLUDED_FILES = {
    "artifacts/enterprise_acceptance.json",
    "artifacts/managed_state_preflight.json",
    "artifacts/backup_restore.json",
    "artifacts/load_smoke.json",
    "artifacts/deployment_evidence.json",
    "artifacts/field_qualification_evidence.json",
    "artifacts/production_preflight.json",
    "artifacts/phase5_diagnostics.json",
    "artifacts/synthetic_acceptance.json",
    "artifacts/clean_extract_validation.json",
    "artifacts/production_topology_template.json",
}


def should_exclude(relative: Path) -> bool:
    if relative.as_posix() in EXCLUDED_FILES:
        return True
    parts = relative.parts
    if any(part in EXCLUDED_DIR_NAMES or part.endswith(".egg-info") for part in parts):
        return True
    # Raw/local external datasets are never distributed. Do not confuse this with
    # the source package `src/pdm_intelligence/external`, which must be shipped.
    if len(parts) >= 2 and parts[0] == "data" and parts[1] in {"external", "raw"}:
        return True
    if len(parts) >= 2 and parts[0] == "data" and parts[1] == "synthetic":
        return True
    return relative.name == ".env" or relative.suffix in EXCLUDED_SUFFIXES


def package_repository(repo: Path, output_zip: Path) -> str:
    repo = repo.resolve()
    output_zip = output_zip.resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pdm-release-") as td:
        stage = Path(td) / ROOT_NAME
        stage.mkdir(parents=True)
        for source in sorted(repo.rglob("*")):
            relative = source.relative_to(repo)
            if should_exclude(relative):
                continue
            target = stage / relative
            if source.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        if output_zip.exists():
            output_zip.unlink()
        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(stage.parent))
    digest = hashlib.sha256(output_zip.read_bytes()).hexdigest()
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the clean predictive-maintenance release ZIP")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    digest = package_repository(Path.cwd(), Path(args.output))
    print(f"RELEASE_ZIP_SHA256 {digest}")


if __name__ == "__main__":
    main()
