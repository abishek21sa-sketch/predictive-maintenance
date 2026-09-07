from pathlib import Path


def test_windows_acceptance_uses_structured_python_candidate():
    script = Path("scripts/v1_acceptance.ps1").read_text(encoding="utf-8")
    assert "[PSCustomObject]" in script
    assert "Executable = $exe" in script
    assert "$launcher = $python.Executable" in script
    assert "$pythonCmd[0]" not in script
    assert "return @($resolved.Source) + $PrefixArgs" not in script


def test_phase5_acceptance_rebuilds_clean_env_and_requires_full_real_data():
    script = Path("scripts/phase5_acceptance.ps1").read_text(encoding="utf-8")
    assert "Get-PythonCandidate" in script
    assert "Removing existing repository-local .venv" in script
    assert 'Remove-Item -LiteralPath ".venv" -Recurse -Force' in script
    assert "Could not remove .venv." in script
    assert '@("-3.13")' in script
    assert "prepare_metropt3.py --download" in script
    assert "phase5_diagnostics.py" in script
    assert "GUROBI_PHASE5_REAL_DATA_PASS" in script
    assert "PHASE5_REAL_OPERATIONS_ACCEPTANCE_PASS" in script


def test_supported_python_policy_matches_package_ci_and_container():
    assert 'requires-python = ">=3.12,<3.15"' in Path("pyproject.toml").read_text(encoding="utf-8")
    assert Path(".python-version").read_text(encoding="utf-8").strip() == "3.13"
    assert Path("Dockerfile").read_text(encoding="utf-8").startswith("FROM python:3.13-slim")
