from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from package_release import package_repository

ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {'.py', '.md', '.toml', '.yml', '.yaml', '.json', '.html', '.css', '.js', '.ps1', '.txt', '.csv', '.example'}
SKIP_DIRS = {'.venv', '.git', '__pycache__', '.pytest_cache', '.ruff_cache', 'build', 'dist', 'data/external', 'data/external_models'}
SECRET_PATTERNS = {
    'openai_api_key': re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b'),
    'google_api_key': re.compile(r'\bAIza[0-9A-Za-z_-]{20,}\b'),
    'slack_token': re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b'),
    'aws_access_key': re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'gurobi_wls_secret': re.compile(r'WLSSECRET\s*=\s*[^\s#<]+', re.IGNORECASE),
}
PRIVATE_WINDOWS_PATH = re.compile(r'\b[A-Za-z]:\\Users\\[^\\\s]+', re.IGNORECASE)


def _iter_public_text_files():
    for path in ROOT.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == d or rel.startswith(d + '/') for d in SKIP_DIRS):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {'.env.example', '.gitignore'}:
            yield path


def scan() -> dict:
    findings: list[dict] = []
    for path in _iter_public_text_files():
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for name, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                token = match.group(0)
                if 'replace-with' in token.lower() or '<' in token:
                    continue
                findings.append({'type': name, 'file': rel})
        if PRIVATE_WINDOWS_PATH.search(text):
            findings.append({'type': 'private_windows_path', 'file': rel})

    forbidden_files = []
    for pattern in ['.env', '*.lic', '*.pem', '*.key', 'gurobi.lic']:
        for path in ROOT.rglob(pattern):
            if path.is_file() and path.name != '.env.example':
                rel = path.relative_to(ROOT).as_posix()
                if not rel.startswith('.venv/'):
                    forbidden_files.append(rel)

    raw_files = [p.relative_to(ROOT).as_posix() for p in (ROOT / 'data' / 'raw').glob('*') if p.is_file() and p.name != '.gitkeep']
    metropt_raw_root = ROOT / 'data' / 'external' / 'metropt3' / 'raw'
    metropt_raw_files = [
        p.relative_to(ROOT).as_posix() for p in metropt_raw_root.rglob('*') if p.is_file()
    ] if metropt_raw_root.exists() else []

    release_probe = ROOT / 'artifacts' / 'release_audit_probe.zip'
    package_repository(ROOT, release_probe)
    with zipfile.ZipFile(release_probe) as archive:
        release_names = archive.namelist()
    release_probe.unlink(missing_ok=True)
    packaged_raw_files = [
        name for name in release_names
        if (('/data/raw/' in name or '/data/external/' in name) and not name.endswith('/.gitkeep'))
    ]

    required = [
        'README.md', 'LICENSE', '.env.example', '.gitignore', '.dockerignore', 'pyproject.toml',
        'Dockerfile', 'docker-compose.yml', 'docker-compose.managed.yml', '.env.managed.example',
        'docs/TECHNICAL_METHODS.md', 'docs/ARCHITECTURE.md', 'docs/VALIDATION.md',
        'docs/RELEASE_ACCEPTANCE_V1.0.md', 'RELEASE_NOTES_V1.0.md', 'RELEASE_MANIFEST.json',
        'scripts/v1_acceptance.ps1', 'scripts/v1_diagnostics.py', 'scripts/v1_benchmark.py',
        'docs/METROPT3_REAL_OPERATIONS.md', 'scripts/prepare_metropt3.py',
        'scripts/metropt_candidate_benchmark.py',
        'scripts/benchmark_cmapss_regimes.py',
        'artifacts/reports/cmapss_regime_benchmark.json',
        'docs/evidence/NASA_CMAPSS_REGIME_VALIDATION.md',
        'docs/MATH_ML_AI_METHODS.md', 'docs/NEXT_LEVEL_READINESS.md',
        'scripts/phase5_diagnostics.py', 'scripts/phase5_acceptance.ps1',
        'scripts/phase5_preflight.py', 'scripts/cmms_erp_acceptance.ps1',
        'scripts/enterprise_acceptance.py', 'scripts/enterprise_acceptance.ps1',
        'scripts/managed_state_preflight.py', 'scripts/backup_restore.py', 'scripts/load_smoke.py',
        'scripts/deployment_evidence.py', 'scripts/managed_state_migrate.py',
        'scripts/production_preflight.py',
        'config/deployment_evidence.template.json',
        'scripts/field_qualification_evidence.py',
        'scripts/release_consistency.py',
        'config/field_qualification_evidence.template.json',
        'docs/ENTERPRISE_ACCEPTANCE.md', 'docs/DEPLOYMENT_EVIDENCE.md',
        'tests/test_cmms_integration.py', 'docs/INTEGRATIONS.md',
        'tests/test_managed_state_adapter.py',
        'docs/PORTFOLIO_READINESS.md', 'docs/IMPLEMENTATION_PROGRESS.md',
        'src/pdm_intelligence/integrations/cmms.py',
        'src/pdm_intelligence/integrations/inventory.py',
        'src/pdm_intelligence/monitoring/runtime.py',
        'src/pdm_intelligence/storage/managed_state.py',
        'src/pdm_intelligence/storage/database.py',
        'src/pdm_intelligence/models/model_registry.py',
        'src/pdm_intelligence/security/deployment_evidence.py',
        'src/pdm_intelligence/security/field_validation_evidence.py',
    ]
    missing_required = [x for x in required if not (ROOT / x).exists()]

    managed_compose = (ROOT / 'docker-compose.managed.yml').read_text(encoding='utf-8') if (ROOT / 'docker-compose.managed.yml').exists() else ''
    container_checks = {
        'managed_postgres_service_present': 'image: postgres:' in managed_compose,
        'managed_password_is_parameterized': 'POSTGRES_PASSWORD: ${PDM_POSTGRES_PASSWORD:?' in managed_compose,
        'managed_runtime_selects_adapter': 'PDM_STATE_BACKEND: managed' in managed_compose and 'PDM_MANAGED_STATE_ADAPTER: sqlalchemy' in managed_compose,
        'container_healthcheck_uses_readiness': '/api/health/ready' in (ROOT / 'Dockerfile').read_text(encoding='utf-8') if (ROOT / 'Dockerfile').exists() else False,
    }

    stale_public = [
        'PHASE1_MANIFEST.json', 'PHASE2B_MANIFEST.json', 'PHASE2C_MANIFEST.json',
        'PHASE2D_MANIFEST.json', 'PHASE3_MANIFEST.json', 'PHASE4_MANIFEST.json',
        'RELEASE_NOTES_v0.2.0.md', 'docs/RELEASE_ACCEPTANCE_v0.2.0.md',
    ]
    stale_present = [x for x in stale_public if (ROOT / x).exists()]

    gitignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
    gitignore_checks = {
        'env_ignored': any(line.strip() == '.env' for line in gitignore.splitlines()),
        'venv_ignored': '.venv/' in gitignore,
        'gurobi_license_ignored': '*.lic' in gitignore or 'gurobi.lic' in gitignore,
        'external_data_ignored': 'data/external/' in gitignore or 'data/external/*' in gitignore,
    }

    checks = {
        'no_secret_patterns': not findings,
        'no_forbidden_secret_files': not forbidden_files,
        'no_raw_nasa_data_redistributed': not any('/data/raw/' in name for name in packaged_raw_files),
        'no_raw_metropt_data_redistributed': not any('/data/external/' in name for name in packaged_raw_files),
        'required_release_files_present': not missing_required,
        'managed_container_boundary': all(container_checks.values()),
        'no_stale_root_release_files': not stale_present,
        'gitignore_release_safety': all(gitignore_checks.values()),
    }
    return {
        'release': '1.0.0',
        'overall': 'PASS' if all(checks.values()) else 'FAIL',
        'checks': checks,
        'findings': findings,
        'forbidden_files': forbidden_files,
        'raw_files': raw_files,
        'metropt_raw_files': metropt_raw_files,
        'packaged_raw_files': packaged_raw_files,
        'missing_required': missing_required,
        'stale_present': stale_present,
        'gitignore': gitignore_checks,
        'container': container_checks,
    }


def main() -> None:
    result = scan()
    out = ROOT / 'artifacts' / 'release_audit.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(result, indent=2))
    if result['overall'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
