$ErrorActionPreference = "Stop"
Write-Host "Predictive Maintenance Intelligence - Phase 1 Acceptance"

function Get-PythonCandidate {
    param(
        [string]$Command,
        [string[]]$PrefixArgs = @()
    )

    try {
        $resolved = Get-Command $Command -ErrorAction Stop
        $exe = $resolved.Source
        if (-not $exe) { $exe = $resolved.Path }
        if (-not $exe) { return $null }

        $versionText = & $exe @PrefixArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $versionText) { return $null }

        $parts = $versionText.Trim().Split('.')
        $major = [int]$parts[0]
        $minor = [int]$parts[1]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 12)) { return $null }

        # Return an object, not an array. PowerShell collapses single-element arrays,
        # which previously turned C:\... into the single character "C" when indexed.
        return [PSCustomObject]@{
            Executable = $exe
            Arguments  = @($PrefixArgs)
            Version    = "$major.$minor"
        }
    }
    catch {
        return $null
    }
}

$python = $null
$candidates = @(
    @{ Command = "py"; Args = @("-3.13") },
    @{ Command = "py"; Args = @("-3.12") },
    @{ Command = "py"; Args = @("-3.14") },
    @{ Command = "py"; Args = @("-3") },
    @{ Command = "python"; Args = @() }
)

foreach ($candidate in $candidates) {
    $probe = Get-PythonCandidate -Command $candidate.Command -PrefixArgs $candidate.Args
    if ($null -ne $probe) {
        $python = $probe
        break
    }
}

if ($null -eq $python) {
    Write-Host "ERROR: A supported Python 3.12+ runtime was not detected." -ForegroundColor Red
    Write-Host "Detected Python Launcher environments (if any):"
    try { py -0p } catch { }
    throw "Install/use Python 3.12+ and rerun this same script."
}

$launcher = $python.Executable
$launcherArgs = @($python.Arguments)
Write-Host "Using Python $($python.Version): $launcher $($launcherArgs -join ' ')"

$venvPython = Join-Path $PWD ".venv\Scripts\python.exe"
if ((Test-Path ".venv") -and -not (Test-Path $venvPython)) {
    Write-Host "Removing incomplete .venv from an earlier failed bootstrap..."
    Remove-Item -Recurse -Force ".venv"
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    & $launcher @launcherArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
}

if (-not (Test-Path $venvPython)) {
    throw "Virtual environment was not created correctly: $venvPython is missing."
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }

& $venvPython -m pip install -e ".[dev,gurobi]"
if ($LASTEXITCODE -ne 0) { throw "Project dependency installation failed." }

& $venvPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Automated tests failed." }

& $venvPython scripts\phase1_diagnostics.py
if ($LASTEXITCODE -ne 0) { throw "Phase 1 diagnostics failed." }

Write-Host "Checking Gurobi runtime/license..."
& $venvPython -c "import gurobipy as gp; m=gp.Model('license_check'); x=m.addVar(vtype=gp.GRB.BINARY); m.setObjective(x,gp.GRB.MAXIMIZE); m.optimize(); assert m.Status==gp.GRB.OPTIMAL; print('GUROBI_ACCEPTANCE_PASS', gp.gurobi.version())"
if ($LASTEXITCODE -ne 0) { throw "Gurobi runtime/license acceptance failed." }

Write-Host "PHASE1_ACCEPTANCE_PASS" -ForegroundColor Green
