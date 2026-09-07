# Enterprise Acceptance Pack

The repository now has one bounded acceptance command for the production
readiness lanes plus model governance. It verifies the reference implementation locally and reports site
specific deployment work as `BLOCKED_EXTERNAL`; it never turns placeholder
configuration into a production certification.

Run the focused pass on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\enterprise_acceptance.ps1
```

For a fast local data-path check when site data is unavailable, run the separate test-only fixture
acceptance:

```powershell
& ".\.venv\Scripts\python.exe" scripts\synthetic_acceptance.py --rows 200000 --assets 1000
```

This verifies deterministic generation, canonical ingestion, replay and provenance. It does not
change the real MetroPT gate. `--model-check` additionally exercises the heavier external RUL
training path on a deterministic bounded sample and still keeps promotion disabled for synthetic
evidence. Use `--model-check-max-rows 200000` only when a full-data local training run is intended.

Run the complete warning-clean regression suite as part of the pass:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\enterprise_acceptance.ps1 -Full
```

Use `-Strict` when a deployment pipeline should fail while any external
production blocker remains. The report is written to
`artifacts/enterprise_acceptance.json`.

Run-specific acceptance reports, including `artifacts/phase5_diagnostics.json`,
remain local evidence and are excluded from the distributable ZIP.

The narrower deployment gate is also available for release orchestration:
`scripts/production_preflight.py` exits `0` only when every readiness blocker
is clear and exits `2` otherwise.

The acceptance lanes are:

1. real-data model-quality gate;
2. managed state, identity, secrets, TLS, observability, recovery, connector and change-control declarations;
3. prospective field-qualification evidence boundary;
4. local backup/restore round trip;
5. CMMS/ERP reference contract and audit linkage;
6. clean release/change-control artifact integrity;
7. runtime request correlation, drift evidence and load-smoke tooling;
8. hash-registered model governance and human promotion;
9. clean extraction of the distributable release ZIP;
10. static validation of the production Kubernetes topology template.

The Kubernetes template includes a suspended managed-state migration Job. A
deployment pipeline must render the site-specific values, run the validator in
strict mode, and explicitly unsuspend/apply the migration Job under an approved
change before the application rollout. The application Deployment does not
perform schema mutation during startup.

The release artifact is also checked after clean extraction. Rebuild the ZIP,
then run:

```powershell
& .\.venv\Scripts\python.exe scripts\clean_extract_validate.py `
  --archive .\dist\Predictive_Maintenance_Intelligence_Final.zip
```

This gate verifies ZIP CRC integrity, safe relative paths, no nested archives,
no raw external datasets or credential material, required release files, valid
manifest JSON, and a real temporary-directory extraction. Its report is written
to `artifacts/clean_extract_validation.json` and is excluded from the
distributable ZIP because it is run-specific evidence.

Production identity/state/TLS/observability/recovery/connector/change-control
lanes remain blocked until the environment declarations, bounded managed-state
probe, and non-secret deployment evidence attestation are present. The
attestation must also be bound to the running `PDM_RELEASE_ID`,
`PDM_DEPLOYMENT_ENVIRONMENT`, and exact `PDM_RELEASE_ARTIFACT_SHA256`; a stale,
cross-environment, or cross-artifact record is rejected,
and change control must explicitly report `PDM_CHANGE_TICKET_STATUS=approved`.

The prospective field-qualification lane remains blocked until a site-owned,
release-bound `PDM-FIELD-QUALIFICATION.v1` attestation records completed
follow-up, reconciled outcomes, safety review, operator acceptance, and
references to the underlying evidence. Historical datasets, modeled savings,
synthetic work orders, and local smoke tests cannot satisfy this lane.

The container boundary also excludes local raw datasets and credentials through
`.dockerignore`; release packaging independently applies the same data and
secret exclusion policy.

`PASS_REFERENCE_ONLY` is intentionally distinct from a production pass. A
real managed database, enterprise identity provider, trusted TLS ingress,
managed observability, tested disaster recovery, site CMMS connector, approved
change record, and prospective field validation are still required before
deployment review.

Managed-state acceptance also includes a runtime safety check: the managed
PostgreSQL adapter must be explicitly selected and all stateful stores must use
the same SQLAlchemy connection boundary. If the adapter, driver, or database is
unavailable, stateful requests fail closed with
`MANAGED_STATE_RUNTIME_UNAVAILABLE`; they never fall back to SQLite.

The generated JSON report is run-specific evidence and is intentionally kept
outside the distributable ZIP. Re-run the command after every release build so
the report describes the exact artifact currently under review.
