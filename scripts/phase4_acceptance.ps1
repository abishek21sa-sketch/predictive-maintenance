$ErrorActionPreference = "Stop"
Write-Host "Predictive Maintenance Intelligence - Phase 4 Decision Intelligence Acceptance"

function Get-PythonCandidate {
    param([string]$Command, [string[]]$PrefixArgs = @())
    try {
        $resolved = Get-Command $Command -ErrorAction Stop
        $exe = $resolved.Source
        if (-not $exe) { $exe = $resolved.Path }
        if (-not $exe) { return $null }
        $versionText = & $exe @PrefixArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $versionText) { return $null }
        $parts = $versionText.Trim().Split('.')
        $major = [int]$parts[0]; $minor = [int]$parts[1]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 12)) { return $null }
        return [PSCustomObject]@{ Executable=$exe; Arguments=@($PrefixArgs); Version="$major.$minor" }
    } catch { return $null }
}

$python = $null
$candidates = @(
    @{ Command="py"; Args=@("-3.13") },
    @{ Command="py"; Args=@("-3.12") },
    @{ Command="py"; Args=@("-3.14") },
    @{ Command="py"; Args=@("-3") },
    @{ Command="python"; Args=@() }
)
foreach ($candidate in $candidates) {
    $probe = Get-PythonCandidate -Command $candidate.Command -PrefixArgs $candidate.Args
    if ($null -ne $probe) { $python=$probe; break }
}
if ($null -eq $python) { throw "A supported Python 3.12+ runtime was not detected." }
$launcher=$python.Executable; $launcherArgs=@($python.Arguments)
Write-Host "Using Python $($python.Version): $launcher $($launcherArgs -join ' ')"

$venvPython = Join-Path $PWD ".venv\Scripts\python.exe"
if ((Test-Path ".venv") -and -not (Test-Path $venvPython)) { Remove-Item -Recurse -Force ".venv" }
if (-not (Test-Path $venvPython)) {
    & $launcher @launcherArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
}
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e ".[dev,gurobi]"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $venvPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Automated tests failed." }
& $venvPython scripts\phase1_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 1 regression diagnostics failed." }
& $venvPython scripts\phase2_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 2 regression diagnostics failed." }
& $venvPython scripts\phase2c_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Observatory regression diagnostics failed." }
& $venvPython scripts\phase3_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 3 external-data diagnostics failed." }
& $venvPython scripts\phase4_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 4 decision-intelligence diagnostics failed." }

Write-Host "Checking licensed Gurobi uncertainty-aware portfolio..."
& $venvPython -c "from collections import Counter; from pdm_intelligence.api.main import release_snapshot; from pdm_intelligence.decision.intelligence import build_decision_intelligence_plan; p=build_decision_intelligence_plan(release_snapshot()['assets'], solver='gurobi', n_rul_scenarios=7, seed=4401); e=p['solver_evidence']; r=p['risk_evidence']; assert e['solver']=='gurobi' and e['status'] in {'OPTIMAL','TIME_LIMIT'}; assert r['cvar_cost']>=r['expected_cost']; assert p['precheck']['feasible_by_precheck']; print('GUROBI_PHASE4_PASS', e['status'], e['mip_gap'], Counter(x['action'] for x in p['choices']))"
if ($LASTEXITCODE -ne 0) { throw "Gurobi Phase 4 acceptance failed." }

Write-Host "PHASE4_DECISION_INTELLIGENCE_ACCEPTANCE_PASS" -ForegroundColor Green
Write-Host "Browser: run .\scripts\start_windows.ps1 then open http://127.0.0.1:8001/stress-lab"
