param([int]$Port = 8001)
$ErrorActionPreference = "Stop"
if ($Port -lt 1 -or $Port -gt 65535) { throw "Port must be between 1 and 65535." }
$python = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Run .\scripts\v1_acceptance.ps1 first to create .venv." }
Write-Host "Starting Reliability Observatory at http://127.0.0.1:$Port"
& $python -m uvicorn pdm_intelligence.api.main:app --host 127.0.0.1 --port $Port
