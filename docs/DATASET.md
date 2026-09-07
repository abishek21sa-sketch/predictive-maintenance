# Dataset and Evidence Tracks

The platform intentionally uses more than one dataset because different predictive-maintenance claims require different evidence.

## 1. NASA C-MAPSS FD001 — reproducible RUL benchmark

NASA C-MAPSS FD001 remains the canonical **simulated RUL benchmark**. It provides run-to-failure engine trajectories and supports reproducible RUL evaluation. The bundled benchmark runtime expects the complete FD001 sensor/operating-setting contract and is never transferred to unrelated equipment.

Training RUL is derived from the final observed failure cycle for the benchmark training engines. Synthetic fixtures exist strictly for tests and are not benchmark evidence.

## 2. Generic external datasets — capability-gated user data

CSV/JSON/SQL/user data enters through the canonical asset, telemetry and maintenance contracts. A successful ingest does not imply RUL readiness. Dataset-specific RUL training requires an explicit RUL target and at least six independent asset trajectories so a leakage-safe holdout can contain at least two complete trajectories while four remain for training.

## 3. MetroPT-3 — real operational compressor evidence

MetroPT-3 is a separate real-data track: railway Air Production Unit compressor telemetry, 1,516,948 source observations and 15 analogue/digital sensor signals. The source is acquired locally from UCI and is not redistributed in the repository.

The row-level dataset is unlabeled; failure windows are published separately. The implementation therefore performs anomaly and failure-horizon modeling rather than fabricating certified row-level RUL. Full preparation/evidence requirements are documented in `docs/METROPT3_REAL_OPERATIONS.md`.

## 4. Synthetic development fixture — test-only

The repository includes a deterministic generator for a 200,000+ row, multi-asset compressor-like
dataset. It follows the same canonical asset, telemetry and maintenance contracts and is intended
for local UI, ingestion, replay, load and model-lifecycle testing when a public or site-owned
dataset is not yet available.

Run `& ".\.venv\Scripts\python.exe" scripts/generate_synthetic_dataset.py --rows 200000 --assets 1000` to write the bundle.
Every generated bundle is marked `SYNTHETIC_TEST_ONLY` with `promotion_allowed: false`. Synthetic
metrics cannot be used as MetroPT evidence, real-data model approval, or prospective field
qualification.
