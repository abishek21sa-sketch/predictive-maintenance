param(
    [switch]$Full,
    [switch]$Strict
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Repository .venv is missing. Run .\scripts\v1_acceptance.ps1 first."
}

$arguments = @("scripts\enterprise_acceptance.py", "--run-tests")
if ($Full) { $arguments += "--full" }
if ($Strict) { $arguments += "--strict" }
& $python @arguments
exit $LASTEXITCODE
