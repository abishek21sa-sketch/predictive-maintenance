# Portfolio Readiness Assessment — 2026-09-01

## Current position

The repository is a strong predictive-maintenance reference platform and
release candidate. It is not yet a certified production deployment. The
CMMS/ERP reference exchange is now implemented, but a real production rollout
still requires a site-specific connector, enterprise identity, managed
retention, operational SLOs, backup/recovery evidence, and prospective field
validation. The production-readiness API now exposes prospective field
qualification as a separate fail-closed gate rather than allowing historical
or synthetic evidence to satisfy it.

| Expectation | Position | Evidence or remaining work |
|---|---|---|
| AI prognostics and anomaly detection | Reference-platform ready | Benchmark and MetroPT evidence tracks are separated; model claims remain dataset-specific |
| Reliability and industrial engineering | Reference-platform ready | Weibull, survival, FMEA/RCM, economics and KPIs are implemented |
| Operations research and uncertainty | Reference-platform ready | Maintain/Inspect/Defer MILP, capacity, skills, parts, stress and CVaR paths exist |
| Simulation and scenario comparison | Reference-platform ready | Seeded Monte Carlo and policy comparisons are implemented |
| CMMS/ERP exchange | Reference adapter validated | `CMMS-WORK-ORDER.v1` and `CMMS-INVENTORY.v1`, idempotent reconciliation, conflict policy, safe JSONL export and audit linkage; Windows Python 3.13 acceptance passed (13 tests) |
| Identity and authorization | Reference control | API-key principal/RBAC boundary, 32-character uniqueness policy and rotation evidence gate; enterprise OIDC/SSO remains deployment work |
| Audit and approvals | Reference control | Hash-chained local audit, verification/export and human approval boundary; managed immutable retention remains deployment work |
| Certified production deployment | Not yet | Durable multi-user database, site connector, secret manager/rotation evidence, trusted TLS, managed observability/SLOs, DR, load/chaos and approved artifact-bound change evidence |
| Prospective field validation | Not yet | Requires a release-bound site attestation with completed follow-up, reconciled outcomes, safety review and operator acceptance |

The release never transfers NASA benchmark accuracy to unrelated equipment and
never treats a CMMS exchange record as proof that field maintenance occurred.
