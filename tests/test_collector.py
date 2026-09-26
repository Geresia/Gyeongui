from datetime import datetime

from gjeta.collector import CallCounter, paced_interval, parse_window, window_end
from gjeta.timeutil import KST

from helpers import kst


def test_window_same_day_and_crossing_midnight():
    am = parse_window("06:30-10:30")
    assert window_end(kst(2026, 9, 25, 7, 0), am) == kst(2026, 9, 25, 10, 30)
    assert window_end(kst(2026, 9, 25, 11, 0), am) is None
    service = parse_window("05:00-01:00")
    assert window_end(kst(2026, 9, 25, 23, 0), service) == kst(2026, 9, 26, 1, 0)
    assert window_end(kst(2026, 9, 26, 0, 30), service) == kst(2026, 9, 26, 1, 0)
    assert window_end(kst(2026, 9, 26, 3, 0), service) is None


def test_paced_interval_stretches_to_last_until_window_end():
    now, end = kst(2026, 9, 25, 7, 0), kst(2026, 9, 25, 11, 0)   # 4 h left
    assert paced_interval(15, now, end, None) == 15
    assert paced_interval(15, now, end, 1000) == 15               # 960 needed, 1000 left
    assert paced_interval(15, now, end, 480) == 30                # budget halved -> 30 s
    assert paced_interval(15, now, end, 0) == float("inf")


def test_call_counter_persists(tmp_path):
    day = datetime(2026, 9, 25, tzinfo=KST).date()
    CallCounter(tmp_path).add(day, 3)
    assert CallCounter(tmp_path).add(day, 2) == 5


def test_read_raw_supports_gzipped_run_files(tmp_path):
    import gzip
    import json

    from gjeta.collector import raw_files, read_raw
    from gjeta.sources.seoul_realtime import RawResponse

    t = kst(2026, 9, 26, 7, 0)
    raw = RawResponse("s", "u", t, t, 1, 200, {"code": "INFO-000"}, None, None)
    (tmp_path / "raw").mkdir()
    with gzip.open(tmp_path / "raw" / "2026-09-26.run1.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps(raw.to_dict()) + "\n")
    files = raw_files(tmp_path, [t.date()])
    assert len(files) == 1
    assert [r.api_code for r in read_raw(files)] == ["INFO-000"]


def test_minutes_until_window():
    from gjeta.collector import minutes_until_window

    am = parse_window("06:30-10:30")
    assert minutes_until_window(kst(2026, 9, 26, 6, 20), am) == 10
    assert minutes_until_window(kst(2026, 9, 26, 7, 0), am) == 0
    assert minutes_until_window(kst(2026, 9, 26, 10, 31), am) == 19 * 60 + 59
