"""Apply and record the repository managed-state schema migration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdm_intelligence.storage.database import initialize_managed_state_schema

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply the explicit managed PostgreSQL state schema migration"
    )
    parser.add_argument("--output", default="artifacts/managed_state_migrate.json")
    args = parser.parse_args()
    result = initialize_managed_state_schema()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS_MANAGED_MIGRATION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
