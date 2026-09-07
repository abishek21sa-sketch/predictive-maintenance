from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd

from .cmapss import CMAPSS_COLUMNS, validate

SYNTHETIC_EVIDENCE_CLASS = "SYNTHETIC_TEST_ONLY"
SYNTHETIC_CLAIM_BOUNDARY = (
    "Synthetic development data is suitable for pipeline, UI, load and model-lifecycle testing only. "
    "It cannot satisfy real-data model promotion, prospective field qualification, or production approval."
)


def generate_cmapss_fixture(n_units: int = 40, min_cycles: int = 90, max_cycles: int = 180, seed: int = 42) -> pd.DataFrame:
    """Deterministic C-MAPSS-shaped run-to-failure fixture used for CI and demos only."""
    rng = np.random.default_rng(seed)
    rows: list[list[float]] = []
    for unit in range(1, n_units + 1):
        life = int(rng.integers(min_cycles, max_cycles + 1))
        initial_wear = rng.uniform(0.0, 0.08)
        for cycle in range(1, life + 1):
            p = cycle / life
            degradation = initial_wear + p**2.15
            settings = [
                rng.normal(0, 0.002),
                rng.normal(0, 0.0003),
                100.0 + rng.normal(0, 0.01),
            ]
            sensors = []
            for j in range(21):
                direction = -1 if j % 3 == 0 else 1
                sensitivity = 0.15 + (j % 7) * 0.05
                baseline = 10.0 + j * 2.5 + rng.normal(0, 0.05)
                value = baseline + direction * sensitivity * degradation * 10 + rng.normal(0, 0.12)
                sensors.append(value)
            rows.append([unit, cycle, *settings, *sensors])
    return validate(pd.DataFrame(rows, columns=CMAPSS_COLUMNS))


def generate_external_fixture(
    n_rows: int = 200_000,
    n_assets: int = 1_000,
    seed: int = 42,
    start: str = "2024-01-01T00:00:00Z",
) -> dict[str, pd.DataFrame]:
    """Create a deterministic multi-asset external-data bundle for development only.

    The bundle follows the canonical external Data Gateway contracts and deliberately carries a
    synthetic evidence boundary. It is useful for exercising ingestion, replay, model training,
    UI flows and bounded load tests; it is never MetroPT-3 or field evidence.
    """
    if n_assets < 6:
        raise ValueError("n_assets must be at least 6 for independent-asset validation")
    if n_rows < n_assets * 10:
        raise ValueError("n_rows must provide at least 10 observations per asset")

    rng = np.random.default_rng(seed)
    base_rows, remainder = divmod(n_rows, n_assets)
    counts = [base_rows + (1 if index < remainder else 0) for index in range(n_assets)]
    start_at = pd.Timestamp(start, tz="UTC")
    asset_records: list[dict] = []
    telemetry_records: list[dict] = []
    maintenance_records: list[dict] = []

    for index, count in enumerate(counts, start=1):
        asset_id = f"SYN-COMP-{index:03d}"
        life = count + int(rng.integers(24, 80))
        asset_start = start_at + pd.Timedelta(hours=index * 3)
        criticality = "critical" if index <= max(1, n_assets // 12) else "high" if index <= n_assets // 3 else "medium"
        asset_records.append(
            {
                "asset_id": asset_id,
                "asset_type": "compressor",
                "criticality": criticality,
                "status": "operational",
                "commissioned_at": (asset_start - pd.Timedelta(days=730 + index)).isoformat(),
            }
        )

        asset_bias = rng.normal(0.0, 1.0, size=5)
        for cycle in range(1, count + 1):
            progress = cycle / life
            noise = rng.normal(0.0, 1.0, size=5)
            telemetry_records.append(
                {
                    "asset_id": asset_id,
                    "cycle": cycle,
                    "timestamp": (asset_start + pd.Timedelta(minutes=15 * (cycle - 1))).isoformat(),
                    "pressure_bar": 7.8 - 1.15 * progress + 0.06 * noise[0] + 0.03 * asset_bias[0],
                    "temperature_c": 58.0 + 22.0 * progress + 0.45 * noise[1] + 0.25 * asset_bias[1],
                    "vibration_rms": 0.75 + 2.6 * progress**1.55 + 0.08 * noise[2] + 0.05 * asset_bias[2],
                    "motor_current_a": 3.8 + 1.0 * progress + 0.12 * noise[3] + 0.06 * asset_bias[3],
                    "flow_rate_lpm": 112.0 - 19.0 * progress + 0.7 * noise[4] + 0.35 * asset_bias[4],
                    "oil_temperature_c": 53.0 + 17.0 * progress + 0.35 * noise[1],
                    "rul": float(life - cycle),
                }
            )

        failure_at = asset_start + pd.Timedelta(minutes=15 * count)
        maintenance_records.append(
            {
                "asset_id": asset_id,
                "event_type": "corrective_failure",
                "start_time": failure_at.isoformat(),
                "end_time": (failure_at + pd.Timedelta(hours=6 + (index % 4))).isoformat(),
                "downtime_hours": float(6 + (index % 4)),
                "labor_hours": float(8 + (index % 5)),
                "part": f"COMP-KIT-{(index % 8) + 1:02d}",
                "cost": float(1800 + 125 * (index % 12)),
                "failure": True,
            }
        )

    return {
        "assets": pd.DataFrame(asset_records),
        "telemetry": pd.DataFrame(telemetry_records),
        "maintenance": pd.DataFrame(maintenance_records),
    }


def write_external_fixture(
    frames: dict[str, pd.DataFrame],
    output_dir: str | Path,
    *,
    seed: int,
    name: str = "synthetic-development-fixture",
) -> dict:
    """Persist a synthetic external bundle and a machine-readable claim boundary."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    entity_hashes: dict[str, str] = {}
    summaries: dict[str, dict] = {}
    for entity in ("assets", "telemetry", "maintenance"):
        frame = frames[entity]
        path = output / f"{entity}.csv"
        save = frame.copy()
        for column in save.columns:
            if pd.api.types.is_datetime64_any_dtype(save[column]):
                save[column] = save[column].astype(str)
        save.to_csv(path, index=False, lineterminator="\n")
        digest = sha256(path.read_bytes()).hexdigest()
        entity_hashes[entity] = digest
        summaries[entity] = {
            "rows": len(frame),
            "columns": list(frame.columns),
            "sha256": digest,
            "asset_count": int(frame["asset_id"].nunique()),
        }
    manifest = {
        "name": name,
        "created_at": datetime.now(UTC).isoformat(),
        "generator": "pdm_intelligence.data.synthetic.generate_external_fixture",
        "seed": seed,
        "evidence_class": SYNTHETIC_EVIDENCE_CLASS,
        "promotion_allowed": False,
        "claim_boundary": SYNTHETIC_CLAIM_BOUNDARY,
        "entities": summaries,
        "entity_sha256": entity_hashes,
        "fingerprint_sha256": sha256(
            "|".join(f"{key}:{entity_hashes[key]}" for key in sorted(entity_hashes)).encode()
        ).hexdigest(),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
