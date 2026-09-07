$ErrorActionPreference = "Stop"
Write-Host "Predictive Maintenance Intelligence - Phase 2 Acceptance"

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
& $venvPython scripts\phase2_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 2 diagnostics failed." }
& $venvPython scripts\phase2b_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 2B regression diagnostics failed." }
& $venvPython scripts\phase2c_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 2C observatory diagnostics failed." }

Write-Host "Checking licensed Gurobi planner path..."
& $venvPython -c "from pdm_intelligence.api.main import release_snapshot; from pdm_intelligence.planner.horizon import build_intervention_horizon; p=build_intervention_horizon(release_snapshot()['assets'], solver='gurobi'); assert p['feasibility']['feasible']; assert p['solver_evidence']['solver']=='gurobi'; print('GUROBI_PLANNER_PASS', p['asset_count'], p['solver_evidence']['status'], p['solver_evidence']['mip_gap'])"
if ($LASTEXITCODE -ne 0) { throw "Gurobi planner acceptance failed." }

Write-Host "PHASE2C_OBSERVATORY_ACCEPTANCE_PASS" -ForegroundColor Green
Write-Host "Browser: run .\scripts\start_windows.ps1 then open http://127.0.0.1:8001"
