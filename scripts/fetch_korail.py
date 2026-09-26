"""Download Korail run records (actual, day-after) and run plans for one date.

    DATA_GO_KR_KEY=... python scripts/fetch_korail.py --date 2026-09-25 --out data/korail

Writes <out>/info_YYYY-MM-DD.json.gz and <out>/plan_YYYY-MM-DD.json.gz (raw items).
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gjeta.sources.korail_run import fetch_all  # noqa: E402
from gjeta.timeutil import KST  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="run date YYYY-MM-DD (default: yesterday KST)")
    ap.add_argument("--out", type=Path, default=Path("data/korail"))
    ap.add_argument("--plan-days-ahead", type=int, default=1, help="also fetch plans for the next N days")
    args = ap.parse_args()
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        raise SystemExit("DATA_GO_KR_KEY is not set")
    today = datetime.now(KST).date()
    run_date = date.fromisoformat(args.date) if args.date else today - timedelta(days=1)
    args.out.mkdir(parents=True, exist_ok=True)

    jobs = [("travelerTrainRunInfo2", "info", run_date)]
    jobs += [("travelerTrainRunPlan2", "plan", run_date + timedelta(days=d)) for d in range(args.plan_days_ahead + 1)]
    for op, prefix, d in jobs:
        items = fetch_all(op, key, d)
        path = args.out / f"{prefix}_{d.isoformat()}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        print(f"{op} {d}: {len(items)} rows -> {path}")


if __name__ == "__main__":
    main()
