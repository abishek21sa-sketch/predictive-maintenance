"""Run the fail-closed deployment-readiness gate as a pipeline command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdm_intelligence.security.production_readiness import production_readiness

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail a deployment review when production-readiness blockers remain"
    )
    parser.add_argument("--output", default="artifacts/production_preflight.json")
    args = parser.parse_args()
    report = production_readiness()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "blockers": report["blockers"]}, indent=2))
    return 0 if report["status"] == "READY_FOR_DEPLOYMENT_REVIEW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
