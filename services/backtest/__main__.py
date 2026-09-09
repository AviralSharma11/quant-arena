"""`python -m services.backtest` — one run, one report.

No HTTP surface. Task 7.2 builds the screen and whatever endpoint feeds it; building one here
would be building past the task, and 7.1's deliverables list a runner, an interface, a strategy,
metrics, a manifest and a reproducibility test — no API among them.
"""

from __future__ import annotations

import argparse
import sys

from services.backtest.report import to_json, to_text
from services.backtest.runner import run_from_dataset
from services.backtest.strategy import SmaCrossover


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.backtest")
    parser.add_argument("--symbol", default="QAA", help="a symbol in the pinned dataset")
    parser.add_argument(
        "--bar-minutes", type=int, default=5,
        help="simulated minutes per bar; 1 makes open, high, low and close identical",
    )
    parser.add_argument("--fast", type=int, default=10)
    parser.add_argument("--slow", type=int, default=30)
    parser.add_argument("--qty", type=int, default=1, help="units per crossing")
    parser.add_argument(
        "--cash", type=int, default=None,
        help="starting cash in ticks; default is ten times the first bar's open",
    )
    parser.add_argument("--json", action="store_true", help="canonical JSON instead of text")
    args = parser.parse_args(argv)

    try:
        result = run_from_dataset(
            strategy=SmaCrossover(fast=args.fast, slow=args.slow, qty=args.qty),
            symbol=args.symbol,
            bar_minutes=args.bar_minutes,
            initial_cash_ticks=args.cash,
        )
    except (ValueError, KeyError) as exc:
        print(f"backtest: {exc}", file=sys.stderr)
        return 2

    sys.stdout.write(to_json(result) if args.json else to_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
