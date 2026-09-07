"""Generate a deterministic external-data fixture for local development and testing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pdm_intelligence.data.synthetic import (
    SYNTHETIC_EVIDENCE_CLASS,
    generate_external_fixture,
    write_external_fixture,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a 200,000+ row synthetic predictive-maintenance dataset"
    )
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--assets", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default="data/synthetic/dev-fixture")
    args = parser.parse_args()
    if args.rows < 20_000:
        parser.error("--rows must be at least 20000")
    if args.assets < 6:
        parser.error("--assets must be at least 6")

    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    metropt_root = (ROOT / "data" / "external" / "metropt3").resolve()
    if output.resolve() == metropt_root or metropt_root in output.resolve().parents:
        raise SystemExit("Refusing to write synthetic data inside the real MetroPT-3 evidence root")

    frames = generate_external_fixture(args.rows, args.assets, args.seed)
    manifest = write_external_fixture(frames, output, seed=args.seed)
    print(
        json.dumps(
            {
                "status": "PASS",
                "output": str(output),
                "evidence_class": SYNTHETIC_EVIDENCE_CLASS,
                "promotion_allowed": False,
                "rows": len(frames["telemetry"]),
                "assets": int(frames["telemetry"]["asset_id"].nunique()),
                "fingerprint_sha256": manifest["fingerprint_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
