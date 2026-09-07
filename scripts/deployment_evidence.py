"""Validate a non-secret deployment evidence attestation file."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
TEMPLATE_PATH = ROOT / "config" / "deployment_evidence.template.json"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from pdm_intelligence.security.deployment_evidence import load_deployment_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a non-secret deployment evidence attestation")
    parser.add_argument("--input", default=os.getenv("PDM_DEPLOYMENT_EVIDENCE_FILE"))
    parser.add_argument("--output", default="artifacts/deployment_evidence.json")
    parser.add_argument(
        "--write-template",
        help="Write the unverified operator template to this path and exit",
    )
    args = parser.parse_args()
    if args.write_template:
        output = Path(args.write_template)
        if not output.is_absolute():
            output = ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote unverified deployment evidence template: {output}")
        return 0
    report = load_deployment_evidence(args.input)
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS_ATTESTATION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
