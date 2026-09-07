$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot
Write-Host "Predictive Maintenance Intelligence - V1.0 Release Acceptance"
Write-Host "Repository: $repoRoot"

function Get-PythonCandidate {
    param([string]$Command, [string[]]$PrefixArgs = @())
    try {
        $resolved = Get-Command $Command -ErrorAction Stop
        $exe = $resolved.Source
        if (-not $exe) { $exe = $resolved.Path }
        if (-not $exe) { return $null }

        # Never bootstrap V1 from the repository-local environment we are about to rebuild.
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

# Prefer the Windows launcher because an activated stale .venv can shadow `python` on PATH.
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
if ($null -eq $python) { throw "A supported system Python (3.12+) was not detected outside the repository-local .venv." }
$launcher = $python.Executable; $launcherArgs = @($python.Arguments)
Write-Host "Using system Python $($python.Version): $launcher $($launcherArgs -join ' ')"

# Final acceptance must be independent of residue from Phase 1-4 folders.
Write-Host "Scrubbing known pre-V1 workspace residue..."
Get-ChildItem -Path $repoRoot -Filter "PHASE*_MANIFEST.json" -File -ErrorAction SilentlyContinue | Remove-Item -Force
$obsoleteWorkspaceFiles = @(
    "RELEASE_NOTES_v0.2.0.md",
    "docs\RELEASE_ACCEPTANCE_v0.2.0.md"
)
foreach ($name in $obsoleteWorkspaceFiles) {
    $path = Join-Path $repoRoot $name
    if (Test-Path $path) {
        Write-Host "Removing stale pre-V1 artifact: $name"
        Remove-Item -Force $path
    }
}

# Rebuild the local environment every time. This intentionally removes stale/corrupt packages
# such as pip's `~umpy` residue and makes the laptop acceptance closer to a clean extraction.
if (Test-Path ".venv") {
    Write-Host "Removing existing repository-local .venv for clean V1 acceptance..."
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

Write-Host "Running warning-clean regression suite..."
& $venvPython -m pytest -q -W error
if ($LASTEXITCODE -ne 0) { throw "Automated tests or warning gate failed." }

& $venvPython -m compileall -q src tests scripts
if ($LASTEXITCODE -ne 0) { throw "Python compilation failed." }

& $venvPython scripts\phase1_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "AI / Reliability / base OR diagnostics failed." }
& $venvPython scripts\phase2_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Maintenance-planning regression diagnostics failed." }
& $venvPython scripts\phase2b_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Intervention-case regression diagnostics failed." }
& $venvPython scripts\phase2c_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Reliability Observatory diagnostics failed." }
& $venvPython scripts\phase3_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "External-data/model-lifecycle diagnostics failed." }
& $venvPython scripts\phase4_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Decision-intelligence diagnostics failed." }
& $venvPython scripts\v1_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "V1 integration diagnostics failed." }
& $venvPython scripts\release_audit.py
if ($LASTEXITCODE -ne 0) { throw "Release/security audit failed." }
& $venvPython scripts\v1_benchmark.py
if ($LASTEXITCODE -ne 0) { throw "Performance sanity benchmark failed." }

Write-Host "Checking licensed Gurobi V1.0 uncertainty-aware portfolio..."
& $venvPython -c "from collections import Counter; from pdm_intelligence.api.main import release_snapshot; from pdm_intelligence.decision.intelligence import build_decision_intelligence_plan; p=build_decision_intelligence_plan(release_snapshot()['assets'], solver='gurobi', n_rul_scenarios=7, seed=4401); e=p['solver_evidence']; r=p['risk_evidence']; assert e['solver']=='gurobi' and e['status']=='OPTIMAL'; assert r['cvar_cost']>=r['expected_cost']; assert p['precheck']['feasible_by_precheck']; assert abs(float(e['mip_gap'] or 0.0)) < 1e-6; print('GUROBI_V1_PASS', e['status'], e['mip_gap'], Counter(x['action'] for x in p['choices']))"
if ($LASTEXITCODE -ne 0) { throw "Licensed Gurobi V1.0 acceptance failed." }

Write-Host "Running CMMS/ERP integration boundary acceptance..."
& $venvPython -m pytest tests\test_cmms_integration.py -q -W error
if ($LASTEXITCODE -ne 0) { throw "CMMS/ERP integration acceptance failed." }

Write-Host "V1_RELEASE_ACCEPTANCE_PASS" -ForegroundColor Green
Write-Host "Start: .\scripts\start_windows.ps1"
Write-Host "Open: http://127.0.0.1:8001  /data-gateway  /stress-lab  /methodology"
