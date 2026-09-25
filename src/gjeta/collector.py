"""Polling collector (DESIGN §5).

Order per poll: fetch -> append raw response to JSONL (always, even on error)
-> parse -> track -> log a one-line status. Nothing downstream can lose raw data.

Run:  SEOUL_API_KEY=... python -m gjeta.collector
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from .sources.seoul_realtime import RawResponse, fetch_positions, parse_position_response
from .stations import load_default_network
from .timeutil import KST
from .tracking.tracker import TrainTracker

log = logging.getLogger("gjeta.collector")

SERVICE_START_HOUR = 5   # KST
SERVICE_END_HOUR = 1     # KST, next day
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
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield RawResponse.from_dict(json.loads(line))


def raw_files(data_dir: Path, days: list[date] | None = None) -> list[Path]:
    files = sorted((data_dir / "raw").glob("*.jsonl"))
    if days:
        wanted = {d.isoformat() for d in days}
        files = [p for p in files if p.stem in wanted]
    return files


def in_service_window(now: datetime) -> bool:
    h = now.astimezone(KST).hour
    return h >= SERVICE_START_HOUR or h < SERVICE_END_HOUR


def fetch_with_retry(api_key: str) -> RawResponse:
    delay = 2.0
    raw = fetch_positions(api_key)
    for _ in range(RETRIES - 1):
        if raw.error is None:
            break
        time.sleep(delay)
        delay *= 2
        raw = fetch_positions(api_key)
    return raw


def run(api_key: str, data_dir: Path, interval: float, max_calls_per_day: int, ignore_window: bool) -> None:
    store = JsonlRawStore(data_dir)
    tracker = TrainTracker(load_default_network())
    stopping = False

    def _stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    calls_today = 0
    today = datetime.now(KST).date()
    log.info("collector started: interval=%ss, data_dir=%s", interval, data_dir)
    while not stopping:
        started = time.monotonic()
        now = datetime.now(KST)
        if now.date() != today:
            today, calls_today = now.date(), 0
        if not ignore_window and not in_service_window(now):
            time.sleep(min(interval, 60))
            continue
        if max_calls_per_day and calls_today >= max_calls_per_day:
            log.warning("daily call budget (%d) reached; sleeping", max_calls_per_day)
            time.sleep(60)
            continue

        raw = fetch_with_retry(api_key)
        calls_today += 1
        store.append(raw)
        observations = parse_position_response(raw)
        events = [e for o in observations for e in tracker.ingest(o)]
        log.info(
            "api_code=%s http=%s trains=%d events=%d latency=%dms%s",
            raw.api_code, raw.http_status, len(observations), len(events), raw.latency_ms,
            f" error={raw.error}" if raw.error else "",
        )
        time.sleep(max(0.0, interval - (time.monotonic() - started)))
    log.info("collector stopped; tracker stats=%s", tracker.stats)


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll Gyeongui-Jungang real-time positions")
    parser.add_argument("--interval", type=float, default=float(os.environ.get("POLL_INTERVAL_SECONDS", 15)))
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    parser.add_argument("--max-calls-per-day", type=int, default=int(os.environ.get("MAX_CALLS_PER_DAY", 0)))
    parser.add_argument("--ignore-service-window", action="store_true")
    args = parser.parse_args()
    api_key = os.environ.get("SEOUL_API_KEY")
    if not api_key:
        raise SystemExit("SEOUL_API_KEY is not set")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(api_key, args.data_dir, args.interval, args.max_calls_per_day, args.ignore_service_window)


if __name__ == "__main__":
    main()
