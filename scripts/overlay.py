"""Overlay one day of 경의중앙선 tracking with Korail trunk-train stops (DESIGN §8 Conflict step 1).

    python scripts/overlay.py --data-dir ../gj-data --date 2026-09-27

Needs <data-dir>/raw/*.jsonl.gz (our collection) and <data-dir>/korail/info_<date>.json.gz.
For every stop of a trunk train at a station we also track, it lists the 경의중앙선 links
(arrival -> next arrival) touching that station within +-WINDOW minutes and how much
longer they took than that pair's median for the day. Output is evidence to look at,
not a causal claim (SPEC §20): trunk trains passing without stopping are not in the data.
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gjeta.collector import raw_files, read_raw  # noqa: E402
from gjeta.sources.korail_run import infer_train_type, parse_run_info_items  # noqa: E402
from gjeta.sources.seoul_realtime import parse_position_response  # noqa: E402
from gjeta.stations import load_default_network  # noqa: E402
from gjeta.tracking.derive import derive_links  # noqa: E402
from gjeta.tracking.tracker import TrainTracker  # noqa: E402

WINDOW = timedelta(minutes=5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--date", required=True)
    args = ap.parse_args()
    day = date.fromisoformat(args.date)
    network = load_default_network()

    tracker = TrainTracker(network)
    events = []
    for raw in read_raw(raw_files(args.data_dir, [day])):
        for o in parse_position_response(raw):
            if o.service_date == day:
                events.extend(tracker.ingest(o))
    links = [iv for iv in derive_links(events) if iv.kind == "LINK" and iv.usable]
    by_pair = defaultdict(list)
    for iv in links:
        by_pair[(iv.a, iv.b)].append(iv.seconds)
    median = {k: statistics.median(v) for k, v in by_pair.items() if len(v) >= 3}

    path = args.data_dir / "korail" / f"info_{day.isoformat()}.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as f:
        stops = parse_run_info_items(json.load(f))
    ours = {s for p in network.patterns.values() for s in p.stations}
    shared = [s for s in stops if network.resolve(s.station_name) in ours and s.time is not None]
    print(f"경의중앙선 links: {len(links)}  trunk stops at shared stations: {len(shared)}")

    near_excess, far = [], set(range(len(links)))
    for s in sorted(shared, key=lambda s: s.time):
        station = network.resolve(s.station_name)
        hits = []
        for idx, iv in enumerate(links):
            if station not in (iv.a, iv.b) or (iv.a, iv.b) not in median:
                continue
            if iv.start - WINDOW <= s.time <= iv.end + WINDOW:
                excess = iv.seconds - median[(iv.a, iv.b)]
                hits.append(f"{iv.train_run_id[-4:]} {iv.a}->{iv.b} {excess:+.0f}s")
                near_excess.append(excess)
                far.discard(idx)
        if hits:
            kind = infer_train_type(s.train_no, s.line_name)
            print(f"{s.time:%H:%M} {station:6s} {s.train_no} {kind:15s} {s.stop_kind:6s} | " + ", ".join(hits))

    far_excess = [links[i].seconds - median[(links[i].a, links[i].b)] for i in far if (links[i].a, links[i].b) in median]
    if near_excess and far_excess:
        print(f"\nexcess vs daily median  near trunk stop: median {statistics.median(near_excess):+.0f}s (n={len(near_excess)})"
              f"  other: median {statistics.median(far_excess):+.0f}s (n={len(far_excess)})")


if __name__ == "__main__":
    main()
