from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pdm_intelligence.real_data.metropt import prepare_metropt3


def main() -> None:
    parser = argparse.ArgumentParser(description="Acquire, validate, feature and train the real MetroPT-3 operational data track")
    parser.add_argument("--root", default=os.getenv("PDM_METROPT_ROOT", "data/external/metropt3"))
    parser.add_argument("--csv-path", default=None)
    parser.add_argument("--download", action="store_true", help="Download the public UCI archive when the raw CSV is absent")
    parser.add_argument("--allow-partial", action="store_true", help="Developer-only: skip exact full-dataset row/time validation")
    parser.add_argument("--bucket-minutes", type=int, default=10)
    parser.add_argument("--seed", type=int, default=731)
    args = parser.parse_args()
    manifest = prepare_metropt3(
        Path(args.root),
        csv_path=args.csv_path,
        download_if_missing=args.download,
        strict_full_dataset=not args.allow_partial,
        bucket_minutes=args.bucket_minutes,
        seed=args.seed,
    )
    print(json.dumps(manifest, indent=2))
    if manifest["status"] not in {"READY_FOR_REAL_OPERATIONS", "REAL_DATA_PREPARED_MODEL_GATE_BLOCKED"}:
        raise SystemExit(2)
    print("METROPT3_REAL_DATA_PREPARATION_PASS")
    if manifest["model_status"] == "PROMOTION_BLOCKED":
        print("METROPT3_MODEL_PROMOTION_BLOCKED")


if __name__ == "__main__":
    main()
