from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pdm_intelligence.fourx.belief_maint_decision import build_belief_maint_decision


def main() -> int:
    out = ROOT / "artifacts" / "belief_maint"
    out.mkdir(parents=True, exist_ok=True)
    decision = build_belief_maint_decision()
    checks = {
        "gate_authorized": decision["gate"] == "AUTHORIZED",
        "human_review_required": decision["human_review_required"] is True,
        "all_model_checks_pass": all(decision["checks"].values()),
        "actions_present": len(decision["actions"]) == 5,
        "belief_lineage_present": len(decision["belief_summary"]) == 5,
        "three_baselines_present": set(decision["baselines"]) == {"fixed_interval", "risk_rank", "lowest_production_load"},
        "evidence_class_bounded": decision["evidence_class"].startswith("synthetic"),
    }
    payload = {"decision": decision, "product_checks": checks}
    (out / "product_decision_evidence.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    passed = sum(bool(v) for v in checks.values())
    (out / "PRODUCT_INTEGRATION_REPORT.md").write_text(
        "\n".join([
            "# BELIEF-MAINT Product Integration Evidence", "",
            f"Checks: **{passed}/{len(checks)} passed**.", "",
            f"- Decision ID: `{decision['decision_id']}`",
            f"- Gate: **{decision['gate']}**",
            f"- Human review required: **{decision['human_review_required']}**", "",
            decision["operator_note"], "",
            decision["result"]["claim_boundary"],
        ]) + "\n",
        encoding="utf-8",
    )
    print(f"BELIEF_MAINT_PRODUCT_EVIDENCE={passed}/{len(checks)}")
    print(f"BELIEF_MAINT_DECISION_ID={decision['decision_id']}")
    print(f"BELIEF_MAINT_GATE={decision['gate']}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
