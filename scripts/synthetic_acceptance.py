"""Run a bounded, test-only end-to-end acceptance flow on synthetic data."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pdm_intelligence.data.synthetic import (
    SYNTHETIC_CLAIM_BOUNDARY,
    SYNTHETIC_EVIDENCE_CLASS,
    generate_external_fixture,
)
from pdm_intelligence.external.gateway import DataGateway
from pdm_intelligence.external.model_lifecycle import (
    predict_external_rul,
    train_external_rul,
)


def run_acceptance(
    rows: int = 200_000,
    assets: int = 1_000,
    seed: int = 42,
    model_check: bool = False,
    model_check_max_rows: int = 20_000,
) -> dict:
    if rows < 20_000:
        raise ValueError("rows must be at least 20000")
    if assets < 6:
        raise ValueError("assets must be at least 6")
    if model_check and model_check_max_rows < assets:
        raise ValueError("model_check_max_rows must be at least the asset count")

    frames = generate_external_fixture(rows, assets, seed)
    repeat = generate_external_fixture(rows, assets, seed)
    deterministic = all(frames[name].equals(repeat[name]) for name in frames)

    with tempfile.TemporaryDirectory(prefix="pdm-synthetic-acceptance-") as td:
        temp_root = Path(td)
        gateway = DataGateway(temp_root / "external", temp_root / "catalog.db")
        ingest = gateway.ingest_frames(
            "synthetic-development-fixture",
            frames,
            source={"mode": "synthetic_fixture", "generator": "pdm", "seed": seed},
            evidence_class=SYNTHETIC_EVIDENCE_CLASS,
        )
        loaded = gateway.load_dataset(ingest.dataset_id)
        dataset_manifest = json.loads(Path(ingest.manifest_path).read_text(encoding="utf-8"))
        replay = gateway.replay(ingest.dataset_id, upto=25, limit=10_000)
        training = None
        predictions = []
        model_manifest = None
        model_input = None
        if model_check:
            model_input = _bounded_model_input(loaded["telemetry"], model_check_max_rows)
            training = train_external_rul(
                model_input,
                ingest.dataset_id,
                output_root=temp_root / "models",
                catalog_path=temp_root / "catalog.db",
                seed=seed,
            )
            predictions = predict_external_rul(
                model_input.drop(columns=["rul"]),
                training.model_id,
                temp_root / "models",
            )
            model_manifest = json.loads(
                (temp_root / "models" / training.model_id / "manifest.json").read_text(encoding="utf-8")
            )

    checks = {
        "row_count_at_least_20000": len(frames["telemetry"]) >= 20_000,
        "requested_row_count_honored": len(frames["telemetry"]) == rows,
        "independent_assets_at_least_6": frames["telemetry"]["asset_id"].nunique() >= 6,
        "canonical_entities_present": set(frames) == {"assets", "telemetry", "maintenance"},
        "deterministic_seed": deterministic,
        "no_missing_telemetry": not frames["telemetry"].isna().any().any(),
        "gateway_ingest": ingest.dataset_id.startswith("synthetic-development-fixture-"),
        "gateway_replay_bounded": (
            replay["record_count"] > 0
            and max(replay["records"], key=lambda x: x["cycle"])["cycle"] <= 25
        ),
        "dataset_marked_synthetic": dataset_manifest["evidence_class"] == SYNTHETIC_EVIDENCE_CLASS,
        "dataset_promotion_blocked": dataset_manifest["promotion_allowed"] is False,
    }
    if model_check:
        checks.update(
            {
                "model_training_completed": bool(training and training.model_id),
                "technical_model_gate_passed": training.promotion_gate["passed"] is True,
                "synthetic_model_promotion_blocked": training.promotion_gate["promotion_allowed"] is False,
                "model_manifest_marked_synthetic": model_manifest["evidence_class"] == SYNTHETIC_EVIDENCE_CLASS,
                "predictions_cover_all_assets": len(predictions) == assets,
                "model_check_input_bounded": len(model_input) <= model_check_max_rows,
            }
        )
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "evidence_class": SYNTHETIC_EVIDENCE_CLASS,
        "promotion_allowed": False,
        "rows": rows,
        "assets": assets,
        "seed": seed,
        "checks": checks,
        "model_check_requested": model_check,
        "model_check_max_rows": model_check_max_rows,
        "model_check_input_rows": len(model_input) if model_input is not None else None,
        "model_check_sampled": bool(model_input is not None and len(model_input) < len(loaded["telemetry"])),
        "model": (
            {
                "model_id": training.model_id,
                "selected_model": training.selected_model,
                "metrics": training.metrics,
                "baseline_metrics": training.baseline_metrics,
                "promotion_gate": training.promotion_gate,
            }
            if training
            else None
        ),
        "claim_boundary": SYNTHETIC_CLAIM_BOUNDARY,
    }


def _bounded_model_input(telemetry: pd.DataFrame, max_rows: int) -> pd.DataFrame:
    """Select a deterministic per-asset trajectory sample for the optional model check.

    The complete large fixture is still generated, ingested and replayed. Only the optional
    heavier model-training step is bounded so a 200k-row smoke test remains practical on a
    student laptop while retaining every asset and covering each trajectory end-to-end.
    """
    if len(telemetry) <= max_rows:
        return telemetry.sort_values(["asset_id", "cycle"]).reset_index(drop=True)
    ordered = telemetry.sort_values(["asset_id", "cycle"]).reset_index(drop=True)
    groups = list(ordered.groupby("asset_id", sort=True))
    if max_rows < len(groups):
        raise ValueError("model_check_max_rows must be at least the asset count")
    base, remainder = divmod(max_rows, len(groups))
    samples = []
    for index, (_, group) in enumerate(groups):
        count = base + (1 if index < remainder else 0)
        positions = np.linspace(0, len(group) - 1, num=count, dtype=int)
        samples.append(group.iloc[np.unique(positions)])
    return pd.concat(samples, ignore_index=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run synthetic development-data acceptance")
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--assets", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="artifacts/synthetic_acceptance.json")
    parser.add_argument(
        "--model-check",
        action="store_true",
        help="also run the heavier dataset-specific RUL training check",
    )
    parser.add_argument(
        "--model-check-max-rows",
        type=int,
        default=20_000,
        help="maximum rows used by --model-check; all assets remain represented",
    )
    args = parser.parse_args()
    report = run_acceptance(
        args.rows,
        args.assets,
        args.seed,
        args.model_check,
        args.model_check_max_rows,
    )
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
