from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdm_intelligence.data.cmapss import add_training_rul, load_trajectory
from pdm_intelligence.models.rul import train_rul_models
from pdm_intelligence.service.pipeline import run_demo


def main():
    parser = argparse.ArgumentParser(description="Predictive Maintenance Intelligence Platform")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--out", default="artifacts/reports/demo_evidence.json")
    train = sub.add_parser("train-cmapss")
    train.add_argument("train_file")
    args = parser.parse_args()
    if args.command == "demo":
        result = run_demo()
        payload = result.__dict__
        out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps({"evidence": str(out), "metrics": result.metrics, "baseline": result.baseline_metrics}, indent=2))
    elif args.command == "train-cmapss":
        df = add_training_rul(load_trajectory(args.train_file))
        r = train_rul_models(df)
        print(json.dumps({"metrics": r.metrics, "baseline": r.baseline_metrics, "candidates": r.candidate_metrics}, indent=2))


if __name__ == "__main__":
    main()
