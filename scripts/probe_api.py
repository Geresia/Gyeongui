"""Save real API responses to samples/ and print a schema report (DESIGN §2.1 TODO).

Run during service hours (05:00-01:00 KST):
    SEOUL_API_KEY=... python scripts/probe_api.py --count 8 --interval 15

Answers: which fields actually appear, value formats, trainSttus values,
updnLine vs terminal, duplicate trainNo per response, recptnDt update cadence,
and station names that do not match the seed CSV.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gjeta.collector import CallCounter  # noqa: E402
from gjeta.sources.seoul_realtime import fetch_positions  # noqa: E402
from gjeta.stations import load_default_network  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--interval", type=float, default=15)
    ap.add_argument("--out", type=Path, default=Path("samples"))
    ap.add_argument("--line", default="경의중앙선", help="노선명 (예: 경춘선, 중앙선 — ITX-청춘 포함 여부 확인용)")
    args = ap.parse_args()
    key = os.environ.get("SEOUL_API_KEY")
    if not key:
        raise SystemExit("SEOUL_API_KEY is not set")
    args.out.mkdir(exist_ok=True)
    network = load_default_network()

    fields: Counter[str] = Counter()
    status: Counter[str] = Counter()
    updn_terminal: Counter[tuple[str, str]] = Counter()
    unknown_names: Counter[str] = Counter()
    recptn: dict[str, list[str]] = {}
    express: Counter[str] = Counter()

    for i in range(args.count):
        raw = fetch_positions(key, args.line)
        used = CallCounter(Path('data')).add(raw.observed_at.date(), 1)  # shares the daily quota with the collector
        path = args.out / f"position_{args.line}_{raw.observed_at.strftime('%Y%m%dT%H%M%S')}.json"
        path.write_text(json.dumps(raw.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        records = (raw.body or {}).get("realtimePositionList") or [] if isinstance(raw.body, dict) else []
        dup = [k for k, v in Counter(r.get("trainNo") for r in records).items() if v > 1]
        print(f"[{i + 1}/{args.count}] api_code={raw.api_code} http={raw.http_status} "
              f"records={len(records)} duplicate_trainNo={dup} calls_today={used} saved={path}")
        for r in records:
            fields.update(r.keys())
            status[str(r.get("trainSttus"))] += 1
            express[str(r.get("directAt"))] += 1
            updn_terminal[(str(r.get("updnLine")), str(r.get("statnTnm")))] += 1
            for name in (r.get("statnNm"), r.get("statnTnm")):
                if name and network.resolve(name) is None:
                    unknown_names[name] += 1
            recptn.setdefault(str(r.get("trainNo")), []).append(str(r.get("recptnDt")))
        if i + 1 < args.count:
            time.sleep(args.interval)

    print("\nfields:", dict(fields))
    print("trainSttus:", dict(status))
    print("directAt (급행/특급/ITX 구분 후보):", dict(express))
    print("updnLine x terminal:", dict(updn_terminal))
    print("names not in seed CSV:", dict(unknown_names))
    changed = sum(len(set(v)) > 1 for v in recptn.values())
    print(f"recptnDt changed across polls for {changed}/{len(recptn)} trains")


if __name__ == "__main__":
    main()
