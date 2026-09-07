$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$pyCandidate = Join-Path $root ".venv\Scripts\python.exe"
$py = if (Test-Path $pyCandidate) { $pyCandidate } else { "python" }
$env:PYTHONPATH = Join-Path $root "src"
Write-Host "BELIEF-MAINT Windows Acceptance"
& $py -m pytest tests/test_signature_algorithm.py tests/test_signature_algorithm_edge_cases.py tests/test_belief_maint_decision.py tests/test_belief_maint_api.py tests/test_belief_maint_ui_contract.py tests/test_optimization.py tests/test_decision.py -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py scripts/belief_maint_evidence.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py scripts/belief_maint_product_evidence.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py -m py_compile src/pdm_intelligence/fourx/signature_algorithm.py src/pdm_intelligence/fourx/belief_maint_decision.py src/pdm_intelligence/api/main.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "BELIEF_MAINT_WINDOWS_ACCEPTANCE=PASS"
