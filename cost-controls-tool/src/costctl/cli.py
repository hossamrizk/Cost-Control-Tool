import argparse
import sys
from pathlib import Path

from .ai import build_interpreter
from .engine import run, write_outputs
from .money import fmt
from .rules import Thresholds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="costctl",
                                     description="Deterministic project cost-control review")
    parser.add_argument("command", choices=["analyse", "analyze"], nargs="?",
                        default="analyse")
    parser.add_argument("--data", default="data", help="directory of source CSV files")
    parser.add_argument("--out", default="out", help="output directory")
    parser.add_argument("--ai", default="none",
                        choices=["none", "auto", "gemini", "deterministic"],
                        help="interpretation layer (default: none, figures only)")
    parser.add_argument("--movement-abs", default=None,
                        help="override BR-04 absolute threshold, e.g. 2000000")
    args = parser.parse_args(argv)

    thresholds = Thresholds()
    if args.movement_abs:
        from decimal import Decimal
        thresholds = Thresholds(movement_abs=Decimal(args.movement_abs))

    interpreter = None if args.ai == "none" else build_interpreter(args.ai)
    result = run(args.data, thresholds=thresholds, interpreter=interpreter)
    paths = write_outputs(result, args.out)

    s = result.summary
    print(f"run {result.run_id}  engine findings: {len(result.findings)}")
    print(f"  reported VAC    {fmt(s.reported_vac):>16}")
    print(f"  calculated VAC  {fmt(s.calculated_vac):>16}")
    print(f"  adjusted VAC    {fmt(s.adjusted_vac):>16}  ({s.adjusted_vac_pct}% of budget)")
    print(f"  confirmed errors {s.counts['confirmed_errors']}, "
          f"requiring explanation {s.counts['requires_explanation']}")
    if interpreter is not None:
        print(f"  AI provider: {interpreter.provider} ({interpreter.model})")
    for warning in result.warnings:
        print(f"  ! {warning}", file=sys.stderr)
    print(f"  wrote {paths['findings']} and {paths['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
