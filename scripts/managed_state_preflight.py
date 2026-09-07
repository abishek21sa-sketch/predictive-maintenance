"""Run a non-secret managed-database configuration/connectivity preflight."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pdm_intelligence.storage.managed_state import managed_state_connectivity

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check managed database configuration without printing secrets")
    parser.add_argument("--url", help="Managed SQLAlchemy URL; prefer PDM_DATABASE_URL in the environment")
    parser.add_argument("--skip-connect", action="store_true", help="Validate URL shape without connecting")
    parser.add_argument("--output", default="artifacts/managed_state_preflight.json")
    args = parser.parse_args()

    if args.skip_connect:
        from pdm_intelligence.storage.managed_state import describe_database_url

        descriptor = describe_database_url(args.url or os.getenv("PDM_DATABASE_URL"))
        result = {"status": "PASS_CONFIGURATION_ONLY" if descriptor.valid else "NOT_READY", "descriptor": descriptor.to_dict()}
    else:
        result = managed_state_connectivity(args.url)
    result["claim_boundary"] = "A successful connectivity check is not a production certification or disaster-recovery test."

    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"PASS_CONFIGURATION_ONLY", "PASS_MANAGED_ADAPTER"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
