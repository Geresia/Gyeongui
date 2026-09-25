"""Rebuild events / segment runs / dwells from collected raw JSONL and report.

    python scripts/replay.py --data-dir data            # tracking quality + stats summary
    python scripts/replay.py --data-dir data --backtest # leakage-free baseline evaluation

Milestone 1 check (SPEC §58): per-run event coverage is printed so we can see whether
a single train was tracked end to end without gaps.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gjeta.collector import raw_files, read_raw  # noqa: E402
from gjeta.eta.backtest import backtest_day, build_stats, split_by_day  # noqa: E402
from gjeta.eta.evaluation import summarize  # noqa: E402
from gjeta.models import EventType  # noqa: E402
from gjeta.sources.seoul_realtime import parse_position_response  # noqa: E402
from gjeta.stations import load_default_network  # noqa: E402
from gjeta.tracking.derive import derive_links, derive_runs  # noqa: E402
from gjeta.tracking.tracker import TrainTracker  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--min-samples", type=int, default=10)
    args = ap.parse_args()

    network = load_default_network()
    observations = [o for raw in read_raw(raw_files(args.data_dir)) for o in parse_position_response(raw)]
    print(f"observations: {len(observations)}")
    if not observations:
        return

    tracker = TrainTracker(network)
    events = []
    for o in sorted(observations, key=lambda o: o.event_time):
        events.extend(tracker.ingest(o))
    segments, dwells = derive_runs(events)
    print(f"tracker: {tracker.stats}")
    print(f"segment runs: {len(segments)} (usable {sum(s.usable for s in segments)})")
    print(f"dwells: {len(dwells)} (usable {sum(d.usable for d in dwells)})")
    links = derive_links(events)
    for kind in ("LINK", "APPROACH"):
        items = [iv for iv in links if iv.kind == kind]
        print(f"{kind.lower()} intervals: {len(items)} (usable {sum(iv.usable for iv in items)})")

    print("\nper-run coverage (arrivals observed / stations passed):")
    for run in sorted(tracker.runs.values(), key=lambda r: r.first_seen):
        arr = [e for e in events if e.train_run_id == run.train_run_id and e.event_type is EventType.ARRIVAL]
        direct = sum(not e.inferred for e in arr)
        print(f"  {run.train_run_id:24s} -> {run.terminal:10s} arrivals={len(arr):3d} "
              f"observed={direct:3d} gaps={run.gaps} regressions={run.regressions} "
              f"{'TERMINATED' if run.terminated else ''}")

    if args.backtest:
        days = split_by_day(observations)
        ordered = sorted(days)
        all_evals = []
        for i, test_day in enumerate(ordered[1:], start=1):
            train_obs = [o for d in ordered[:i] for o in days[d]]
            stats = build_stats(train_obs, network, args.min_samples)
            evals = backtest_day(days[test_day], stats, network)
            print(f"\nbacktest {test_day}: {len(evals)} scored predictions")
            all_evals.extend(evals)
        print("\nbaseline summary (seconds):")
        for row in summarize(all_evals):
            print("  ", {k: round(v, 1) if isinstance(v, float) else v for k, v in row.items()})


if __name__ == "__main__":
    main()
