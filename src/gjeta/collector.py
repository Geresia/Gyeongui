"""Polling collector (DESIGN §5).

Order per poll: fetch -> append raw response to JSONL (always, even on error)
-> parse -> track -> log a one-line status. Nothing downstream can lose raw data.

Run:  SEOUL_API_KEY=... python -m gjeta.collector --window 06:30-10:30

Call budget: the Seoul real-time subway key allows 1,000 calls/day until a use case
is registered in the Open Data gallery. Every HTTP attempt (retries included) counts;
the count is persisted per KST day so restarts do not reset it. When the remaining
budget cannot cover the rest of the window at --interval, the interval is stretched
so polling lasts until the window ends instead of stopping early.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import signal
import time
from collections.abc import Iterator
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from .sources.seoul_realtime import RawResponse, fetch_positions, parse_position_response
from .stations import load_default_network
from .timeutil import KST
from .tracking.tracker import TrainTracker

log = logging.getLogger("gjeta.collector")

DEFAULT_WINDOW = "05:00-01:00"   # KST service hours; may cross midnight
DEFAULT_DAILY_LIMIT = 1000       # documented limit for the real-time subway key
RETRIES = 3


class JsonlRawStore:
    """Append-only daily files: <dir>/raw/YYYY-MM-DD.jsonl (KST calendar date)."""

    def __init__(self, data_dir: Path):
        self.dir = data_dir / "raw"
        self.dir.mkdir(parents=True, exist_ok=True)

    def append(self, raw: RawResponse) -> None:
        path = self.dir / f"{raw.observed_at.astimezone(KST).date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(raw.to_dict(), ensure_ascii=False) + "\n")


def read_raw(paths: list[Path]) -> Iterator[RawResponse]:
    for path in sorted(paths):
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield RawResponse.from_dict(json.loads(line))


def raw_files(data_dir: Path, days: list[date] | None = None) -> list[Path]:
    # Plain daily files from a local collector, or gzipped per-run files from CI.
    files = sorted([*(data_dir / "raw").glob("*.jsonl"), *(data_dir / "raw").glob("*.jsonl.gz")])
    if days:
        wanted = {d.isoformat() for d in days}
        files = [p for p in files if p.name[:10] in wanted]
    return files


def parse_window(text: str) -> tuple[dtime, dtime]:
    start, end = (dtime.fromisoformat(part.strip()) for part in text.split("-"))
    return start, end


def window_end(now: datetime, window: tuple[dtime, dtime]) -> datetime | None:
    """End of the window containing `now` (KST), or None if `now` is outside it."""
    now = now.astimezone(KST)
    start, end = window
    t = now.time()
    if start <= end:
        inside = start <= t < end
        end_day = now.date()
    else:  # crosses midnight
        inside = t >= start or t < end
        end_day = now.date() + timedelta(days=1) if t >= start else now.date()
    if not inside:
        return None
    return datetime.combine(end_day, end, tzinfo=KST)


def minutes_until_window(now: datetime, window: tuple[dtime, dtime]) -> float:
    """Minutes until the window next opens (0 if inside it)."""
    if window_end(now, window) is not None:
        return 0.0
    now = now.astimezone(KST)
    start = datetime.combine(now.date(), window[0], tzinfo=KST)
    if start <= now:
        start += timedelta(days=1)
    return (start - now).total_seconds() / 60


def paced_interval(interval: float, now: datetime, end: datetime, remaining_calls: int | None) -> float:
    """Stretch the interval so the remaining budget lasts until the window ends."""
    if remaining_calls is None:
        return interval
    if remaining_calls <= 0:
        return float("inf")
    return max(interval, (end - now).total_seconds() / remaining_calls)


class CallCounter:
    """Calls made per KST calendar day, persisted in <data_dir>/calls/YYYY-MM-DD.txt."""

    def __init__(self, data_dir: Path):
        self.dir = data_dir / "calls"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, day: date) -> Path:
        return self.dir / f"{day.isoformat()}.txt"

    def get(self, day: date) -> int:
        p = self._path(day)
        return int(p.read_text().strip() or 0) if p.exists() else 0

    def add(self, day: date, n: int) -> int:
        total = self.get(day) + n
        self._path(day).write_text(str(total))
        return total


def fetch_with_retry(api_key: str, max_attempts: int = RETRIES) -> tuple[RawResponse, int]:
    """Returns the last response and the number of HTTP attempts made (all count to the quota)."""
    delay = 2.0
    raw = fetch_positions(api_key)
    attempts = 1
    while raw.error is not None and attempts < max_attempts:
        time.sleep(delay)
        delay *= 2
        raw = fetch_positions(api_key)
        attempts += 1
    return raw, attempts


def run(api_key: str, data_dir: Path, interval: float, daily_limit: int, window: tuple[dtime, dtime],
        single_window: bool = False, max_wait_minutes: float | None = None) -> None:
    store = JsonlRawStore(data_dir)
    counter = CallCounter(data_dir)
    tracker = TrainTracker(load_default_network())
    stopping = False

    def _stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    log.info("collector started: interval=%ss window=%s-%s daily_limit=%s data_dir=%s",
             interval, window[0], window[1], daily_limit or "none", data_dir)
    collected = False
    if max_wait_minutes is not None and minutes_until_window(datetime.now(KST), window) > max_wait_minutes:
        log.info("window %s-%s is not near; nothing to do", window[0], window[1])
        return
    while not stopping:
        started = time.monotonic()
        now = datetime.now(KST)
        end = window_end(now, window)
        if end is None:
            if single_window and collected:
                break  # window finished (CI mode)
            time.sleep(30)
            continue
        today = now.date()
        remaining = daily_limit - counter.get(today) if daily_limit else None
        wait = paced_interval(interval, now, end, remaining)
        if wait == float("inf"):
            log.warning("daily call limit (%d) reached; waiting for the next day", daily_limit)
            time.sleep(60)
            continue

        collected = True
        raw, attempts = fetch_with_retry(api_key, RETRIES if remaining is None else max(1, min(RETRIES, remaining)))
        used = counter.add(today, attempts)
        store.append(raw)
        observations = parse_position_response(raw)
        events = [e for o in observations for e in tracker.ingest(o)]
        log.info(
            "api_code=%s http=%s trains=%d events=%d latency=%dms calls_today=%d next_in=%.0fs%s",
            raw.api_code, raw.http_status, len(observations), len(events), raw.latency_ms,
            used, wait, f" error={raw.error}" if raw.error else "",
        )
        time.sleep(max(0.0, wait - (time.monotonic() - started)))
    log.info("collector stopped; tracker stats=%s", tracker.stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll Gyeongui-Jungang real-time positions")
    parser.add_argument("--interval", type=float, default=float(os.environ.get("POLL_INTERVAL_SECONDS", 15)))
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    parser.add_argument("--daily-limit", type=int, default=int(os.environ.get("MAX_CALLS_PER_DAY", DEFAULT_DAILY_LIMIT)),
                        help="0 = no limit (only after the gallery registration lifts the quota)")
    parser.add_argument("--window", default=os.environ.get("COLLECT_WINDOW", DEFAULT_WINDOW),
                        help="KST collection window HH:MM-HH:MM, may cross midnight")
    parser.add_argument("--single-window", action="store_true",
                        help="exit once the current/next window has ended (for scheduled CI runs)")
    parser.add_argument("--max-wait-minutes", type=float, default=None,
                        help="exit immediately if the window opens later than this (backup CI schedules)")
    args = parser.parse_args()
    api_key = os.environ.get("SEOUL_API_KEY")
    if not api_key:
        raise SystemExit("SEOUL_API_KEY is not set")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(api_key, args.data_dir, args.interval, args.daily_limit, parse_window(args.window), args.single_window,
        args.max_wait_minutes)


if __name__ == "__main__":
    main()
