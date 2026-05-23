import argparse
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data_quality import DataQualityEvaluator


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate collection, management, and research-data quality.")
    parser.add_argument("--lookback-hours", type=int, default=int(os.getenv("DATA_QUALITY_LOOKBACK_HOURS", "168")))
    parser.add_argument("--json", action="store_true", help="Print raw JSON report.")
    parser.add_argument("--no-persist", action="store_true", help="Do not save the latest report to system metadata.")
    args = parser.parse_args()

    evaluator = DataQualityEvaluator()
    report = evaluator.assess(lookback_hours=args.lookback_hours)
    if not args.no_persist:
        evaluator.save_report(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(evaluator.render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
