$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = Join-Path $root "src"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Repository .venv is missing. Run .\scripts\v1_acceptance.ps1 first."
}

Write-Host "Predictive Maintenance CMMS/ERP Integration Acceptance"
& $venvPython -m pytest tests/test_cmms_integration.py -q -W error
if ($LASTEXITCODE -ne 0) { throw "CMMS/ERP integration tests failed." }

& $venvPython -m py_compile `
    src/pdm_intelligence/integrations/cmms.py `
    src/pdm_intelligence/integrations/inventory.py `
    src/pdm_intelligence/integrations/batch.py `
    src/pdm_intelligence/integrations/__init__.py `
    src/pdm_intelligence/security/auth.py `
    src/pdm_intelligence/governance/audit.py `
    src/pdm_intelligence/api/main.py
if ($LASTEXITCODE -ne 0) { throw "CMMS/ERP Python compilation failed." }

$evidence = [ordered]@{
    status = "PASS"
    generated_at_utc = [DateTime]::UtcNow.ToString("o")
    contract_versions = @("CMMS-WORK-ORDER.v1", "CMMS-INVENTORY.v1")
    test_file = "tests/test_cmms_integration.py"
    security_properties = @(
        "permission-gated API writes",
        "deterministic fingerprints and batch IDs",
        "stale/conflict non-overwrite policy",
        "atomic mirror and audit-event commit",
        "audit-linked reconciliation and export",
        "SHA-256 export-content integrity header",
        "contract-only exchange payloads"
    )
    claim_boundary = "Exchange records are not proof of field maintenance or physical stock."
}
$evidencePath = Join-Path $root "artifacts\cmms_erp_acceptance.json"
$evidence | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $evidencePath -Encoding UTF8
Write-Host "CMMS_ERP_EVIDENCE=$evidencePath"
Write-Host "CMMS_ERP_WINDOWS_ACCEPTANCE=PASS" -ForegroundColor Green
