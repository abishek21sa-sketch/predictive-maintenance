$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot
Write-Host "Predictive Maintenance Intelligence - Phase 5 Real Operations Acceptance"
Write-Host "Repository: $repoRoot"

function Get-PythonCandidate {
    param([string]$Command, [string[]]$PrefixArgs = @())
    try {
        $resolved = Get-Command $Command -ErrorAction Stop
        $exe = $resolved.Source
        if (-not $exe) { $exe = $resolved.Path }
        if (-not $exe) { return $null }
        $localVenv = Join-Path $repoRoot ".venv"
        if ($exe.StartsWith($localVenv, [System.StringComparison]::OrdinalIgnoreCase)) { return $null }
        $versionText = & $exe @PrefixArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $versionText) { return $null }
        $parts = $versionText.Trim().Split('.')
        $major = [int]$parts[0]; $minor = [int]$parts[1]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 12)) { return $null }
        return [PSCustomObject]@{ Executable = $exe; Arguments = @($PrefixArgs); Version = "$major.$minor" }
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
    if ($null -ne $probe) { $python = $probe; break }
}
if ($null -eq $python) { throw "A supported system Python (3.12+) was not detected outside the repository-local .venv." }
$launcher = $python.Executable; $launcherArgs = @($python.Arguments)
Write-Host "Using system Python $($python.Version): $launcher $($launcherArgs -join ' ')"

Write-Host "Rebuilding repository-local .venv for clean Phase 5 acceptance..."
if (Test-Path ".venv") {
    Write-Host "Removing existing repository-local .venv for clean Phase 5 acceptance..."
    try {
        Remove-Item -LiteralPath ".venv" -Recurse -Force -ErrorAction Stop
    } catch {
        throw "Could not remove .venv. Stop the running uvicorn/Python server and close terminals using this repository, then rerun acceptance. Original error: $($_.Exception.Message)"
    }
}
& $launcher @launcherArgs -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "Virtual environment Python was not created." }

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install -e ".[dev,gurobi]"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Host "Running full warning-clean regression suite..."
& $venvPython -m pytest -q -W error
if ($LASTEXITCODE -ne 0) { throw "Regression suite failed." }
& $venvPython -m compileall -q src tests scripts
if ($LASTEXITCODE -ne 0) { throw "Python compilation failed." }

Write-Host "Re-running locked Phase 1-4 / V1 engineering gates..."
& $venvPython scripts\phase1_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 1 diagnostics failed." }
& $venvPython scripts\phase2_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 2 diagnostics failed." }
& $venvPython scripts\phase2b_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 2B diagnostics failed." }
& $venvPython scripts\phase2c_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 2C diagnostics failed." }
& $venvPython scripts\phase3_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 3 diagnostics failed." }
& $venvPython scripts\phase4_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 4 diagnostics failed." }
& $venvPython scripts\v1_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "V1 integration diagnostics failed." }
& $venvPython scripts\phase5_preflight.py
if ($LASTEXITCODE -ne 0) { throw "Phase 5 structural preflight failed." }
& $venvPython scripts\release_audit.py
if ($LASTEXITCODE -ne 0) { throw "Release/security audit failed." }
& $venvPython scripts\v1_benchmark.py
if ($LASTEXITCODE -ne 0) { throw "Performance sanity benchmark failed." }

Write-Host "Acquiring/validating full UCI MetroPT-3 real operational dataset..."
Write-Host "The source archive is about 208 MB. Progress is printed in ~10 MB increments."
& $venvPython scripts\prepare_metropt3.py --download
if ($LASTEXITCODE -ne 0) { throw "MetroPT-3 real-data preparation failed." }

Write-Host "Running end-to-end real operations diagnostics..."
& $venvPython scripts\phase5_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 5 real operations diagnostics failed." }

Write-Host "Checking licensed Gurobi on the real-data-derived APU decision..."
$code = @'
from pathlib import Path
import pandas as pd
from pdm_intelligence.operations.command import build_metropt_operations_case, resource_scenario_from_name
from pdm_intelligence.real_data.metropt import METROPT_FAILURE_EVENTS
root=Path('data/external/metropt3')
at=str(METROPT_FAILURE_EVENTS[-1].start_ts-pd.Timedelta(hours=3))
case=build_metropt_operations_case(root, Path('data/phase5_gurobi_acceptance.db'), at=at, scenario=resource_scenario_from_name('baseline',bays=2), solver='gurobi')
e=case['optimized']['solver_evidence']
assert case['asset']['evidence_class']=='REAL_OPERATIONAL_TELEMETRY'
assert e['solver']=='gurobi' and e['status']=='OPTIMAL'
assert e['mip_gap'] is not None and e['mip_gap'] <= 1e-6
print('GUROBI_PHASE5_REAL_DATA_PASS', e['status'], e['mip_gap'], case['optimized']['target_choice'])
'@
& $venvPython -c $code
if ($LASTEXITCODE -ne 0) { throw "Licensed Gurobi real-data decision acceptance failed." }
if (Test-Path "data\phase5_gurobi_acceptance.db") { Remove-Item "data\phase5_gurobi_acceptance.db" -Force }

Write-Host "PHASE5_REAL_OPERATIONS_ACCEPTANCE_PASS" -ForegroundColor Green
Write-Host "Start: powershell -ExecutionPolicy Bypass -File .\scripts\start_windows.ps1"
Write-Host "Open: http://127.0.0.1:8001/operations"
