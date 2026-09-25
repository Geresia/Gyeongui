"""Synthetic observations in the documented Seoul API shape (not real data)."""

from __future__ import annotations

from datetime import datetime, timedelta

from gjeta.models import PositionObservation
from gjeta.sources.seoul_realtime import parse_position_record
from gjeta.timeutil import KST

STATUS = {"APPROACH": "0", "ARRIVE": "1", "DEPART": "2", "PREV_DEPARTED": "3"}


def record(train_no: str, station: str, status: str, terminal: str, t: datetime, direct: str = "0") -> dict:
    return {
        "subwayId": "1063",
        "subwayNm": "경의중앙선",
        "statnId": "",
        "statnNm": station,
        "trainNo": train_no,
        "recptnDt": t.strftime("%Y-%m-%d %H:%M:%S"),
        "updnLine": "1",
        "statnTnm": terminal,
        "trainSttus": STATUS[status],
        "directAt": direct,
        "lstcarAt": "0",
    }


def obs(train_no: str, station: str, status: str, terminal: str, t: datetime, direct: str = "0") -> PositionObservation:
    o = parse_position_record(record(train_no, station, status, terminal, t, direct), observed_at=t)
    assert o is not None
    return o


def kst(y: int, mo: int, d: int, h: int, mi: int, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=KST)


def simulate_trip(
    train_no: str,
    stations: list[str],
    start: datetime,
    segment_seconds: float,
    dwell_seconds: float,
    poll: float = 10.0,
    terminal: str | None = None,
    approach_seconds: float = 20.0,
) -> list[PositionObservation]:
    """Train starts dwelling at stations[0]; polled every `poll` seconds."""
    terminal = terminal or stations[-1]
    timeline: list[tuple[float, str, str]] = []  # (t_offset, station, status)
    t = 0.0
    for i, s in enumerate(stations):
        timeline.append((t, s, "ARRIVE"))
        if i == len(stations) - 1:
            break
        t += dwell_seconds
        timeline.append((t, s, "DEPART"))
        t += segment_seconds - approach_seconds
        timeline.append((t, stations[i + 1], "APPROACH"))
        t += approach_seconds
    end = t
    out = []
    tick = 0.0
    while tick <= end + poll:
        state = [x for x in timeline if x[0] <= tick][-1]
        out.append(obs(train_no, state[1], state[2], terminal, start + timedelta(seconds=tick)))
        tick += poll
    return out
