"""CLI: python -m app.assurance [--corpus PATH] [--min-f1 FLOAT] [--analyze-case JSON].

No network, tool or governance side effects. Reports are redacted.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.assurance.bengal import BengalCase, BengalEngine
from app.assurance.bengal_eval import load_cases, run_eval


def main() -> int:
    ap = argparse.ArgumentParser(description="BENGAL BAA observation-only assurance")
    ap.add_argument("--corpus", default="../fixtures/bengal/cases.jsonl")
    ap.add_argument("--min-f1", type=float, default=0.85)
    ap.add_argument("--analyze-case", help="path to a single case JSON file")
    args = ap.parse_args()
    engine = BengalEngine(secret="")
    if args.analyze_case:
        case = BengalCase.model_validate(json.loads(Path(args.analyze_case).read_text(encoding="utf-8")))
        report = asyncio.run(engine.analyze(case))
        print(json.dumps(report.summary(), indent=2))
        return 0
    path = Path(args.corpus)
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / "fixtures" / "bengal" / "cases.jsonl"
    report = asyncio.run(run_eval(load_cases(path), engine))
    print(json.dumps(json.loads(report.model_dump_json()), indent=2))
    if not report.verified or report.macro_f1 < args.min_f1:
        print(f"FAIL: macro_f1={report.macro_f1} below {args.min_f1}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
