from pathlib import Path

from fastapi.testclient import TestClient

from pdm_intelligence import __version__
from pdm_intelligence.api.main import app

ROOT = Path(__file__).resolve().parents[1]


def test_v1_version_is_consistent_across_package_api_and_pyproject():
    assert __version__ == '1.0.0'
    assert 'version = "1.0.0"' in (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    with TestClient(app) as client:
        assert client.get('/api/health').json()['version'] == '1.0.0'


def test_v1_methodology_reports_three_policy_stress_comparison():
    with TestClient(app) as client:
        page = client.get('/methodology')
    assert page.status_code == 200
    assert '3 policy comparison' in page.text
    assert 'common random numbers' in page.text
    assert '2 policy results' not in page.text


def test_v1_public_release_files_are_present_and_old_root_manifests_are_gone():
    required = ['LICENSE', 'RELEASE_NOTES_V1.0.md', 'RELEASE_MANIFEST.json', 'docs/RELEASE_ACCEPTANCE_V1.0.md']
    assert all((ROOT / name).exists() for name in required)
    assert not list(ROOT.glob('PHASE*_MANIFEST.json'))
    assert not (ROOT / 'RELEASE_NOTES_v0.2.0.md').exists()
    assert not (ROOT / 'docs' / 'RELEASE_ACCEPTANCE_v0.2.0.md').exists()


def test_windows_startup_points_to_final_acceptance():
    script = (ROOT / 'scripts' / 'start_windows.ps1').read_text(encoding='utf-8')
    assert 'v1_acceptance.ps1' in script
    assert 'Reliability Observatory' in script


def test_technical_methods_has_required_ai_ie_or_sections():
    text = (ROOT / 'docs' / 'TECHNICAL_METHODS.md').read_text(encoding='utf-8')
    assert '## A. Artificial Intelligence' in text
    assert '## B. Industrial Engineering' in text
    assert '## C. Operations Research' in text
    assert 'CVaR' in text
    assert 'exact' in text.lower()


def test_v1_acceptance_rebuilds_local_environment_and_scrubs_known_phase_residue():
    script = (ROOT / 'scripts' / 'v1_acceptance.ps1').read_text(encoding='utf-8')
    assert 'PHASE*_MANIFEST.json' in script
    assert 'docs\\RELEASE_ACCEPTANCE_v0.2.0.md' in script
    assert 'Remove-Item -LiteralPath ".venv" -Recurse -Force' in script
    assert 'Could not remove .venv.' in script
    assert 'Prefer the Windows launcher' in script
    assert 'StartsWith($localVenv' in script
