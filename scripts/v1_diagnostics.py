from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from fastapi.testclient import TestClient
from package_release import package_repository

from pdm_intelligence import __version__
from pdm_intelligence.api.main import app, release_snapshot

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    metropt_raw_root = ROOT / 'data' / 'external' / 'metropt3' / 'raw'
    local_metropt_raw_present = any(p.is_file() for p in metropt_raw_root.glob('*')) if metropt_raw_root.exists() else False
    with tempfile.TemporaryDirectory(prefix='pdm-v1-diagnostic-') as td:
        probe = Path(td) / 'release.zip'
        package_repository(ROOT, probe)
        with zipfile.ZipFile(probe) as archive:
            packaged_metropt_raw = any('/data/external/' in name for name in archive.namelist())
    snap = release_snapshot()
    with TestClient(app) as client:
        health = client.get('/api/health')
        portfolio = client.get('/api/portfolio')
        modes = client.get('/api/data/modes')
        plan = client.get('/api/decision-intelligence/plan?bay_capacity=2&n_rul_scenarios=5')
        stress = client.get('/api/decision-intelligence/stress-test?bay_capacity=2&n_rul_scenarios=5&n_simulations=40')
        pages = {path: client.get(path) for path in ['/', '/data-gateway', '/operations', '/stress-lab', '/methodology']}

    stress_json = stress.json() if stress.status_code == 200 else {}
    plan_json = plan.json() if plan.status_code == 200 else {}
    policies = {row.get('policy') for row in stress_json.get('stress_test', {}).get('results', [])}
    methods_text = pages['/methodology'].text
    pyproject = (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    methods_doc = (ROOT / 'docs' / 'TECHNICAL_METHODS.md').read_text(encoding='utf-8')

    required_methods_sections = all(x in methods_doc for x in [
        '## A. Artificial Intelligence', '## B. Industrial Engineering', '## C. Operations Research'
    ])
    checks = {
        'package_version_1_0_0': __version__ == '1.0.0',
        'api_health_v1': health.status_code == 200 and health.json().get('version') == '1.0.0',
        'pyproject_v1': 'version = "1.0.0"' in pyproject,
        'readme_v1': '**Release:** `1.0.0`' in readme,
        'bundled_fd001_snapshot_100_assets': snap is not None and snap.get('asset_count') == 100,
        'all_primary_pages_200': all(r.status_code == 200 for r in pages.values()),
        'external_data_modes_ready': modes.status_code == 200 and {'browser_text', 'sql', 'historical_replay', 'metropt3_real_operations'} <= {x['id'] for x in modes.json()['modes']},
        'decision_plan_optimal': plan.status_code == 200 and plan_json.get('solver_evidence', {}).get('status') == 'OPTIMAL',
        'decision_cvar_valid': plan.status_code == 200 and plan_json.get('risk_evidence', {}).get('cvar_cost', -1) >= plan_json.get('risk_evidence', {}).get('expected_cost', 0),
        'stress_three_policy_set': policies == {'optimized_intervention', 'earliest_rul_heuristic', 'run_to_failure'},
        'methodology_three_policy_evidence': '3 policy comparison' in methods_text and 'common random numbers' in methods_text,
        'methodology_real_data_track_separated': 'MetroPT-3 stays a separate evidence track' in methods_text and 'no NASA RUL transfer' in methods_text,
        'technical_methods_required_sections': required_methods_sections,
        'raw_nasa_data_not_packaged': not any(p.is_file() and p.name != '.gitkeep' for p in (ROOT / 'data' / 'raw').glob('*')),
        'raw_metropt_not_packaged': not packaged_metropt_raw,
    }
    result = {
        'release': '1.0.0',
        'overall': 'PASS' if all(checks.values()) else 'FAIL',
        'evidence_class': 'V1_RELEASE_INTEGRATION_DIAGNOSTIC',
        'checks': checks,
        'api': {
            'health': health.status_code,
            'portfolio': portfolio.status_code,
            'data_modes': modes.status_code,
            'decision_plan': plan.status_code,
            'stress_test': stress.status_code,
            'pages': {k: v.status_code for k, v in pages.items()},
        },
        'stress_policies': sorted(policies),
        'local_metropt_raw_present': local_metropt_raw_present,
        'claim_boundary': 'V1.0 release integration is validated; prospective field performance remains external validation.',
    }
    out = ROOT / 'artifacts' / 'v1_diagnostics.json'
    out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    if result['overall'] != 'PASS':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
