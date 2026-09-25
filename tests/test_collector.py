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
