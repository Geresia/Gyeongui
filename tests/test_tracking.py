from datetime import timedelta

from gjeta.models import EventType
from gjeta.stations import load_default_network
from gjeta.tracking.derive import derive_runs
from gjeta.tracking.tracker import TrainTracker

from helpers import kst, obs, simulate_trip

NET = load_default_network()


def run_all(observations, include_initial=False, **kw):
    """Most tests look at transitions; the exact event emitted for a run's first
    observation (see test_first_observation_is_an_exact_event) is left out by default."""
    tr = TrainTracker(NET, **kw)
    events, seen = [], set()
    for o in observations:
        out = tr.ingest(o)
        if include_initial or o.train_run_id in seen:
            events.extend(out)
        seen.add(o.train_run_id)
    return tr, events


def test_first_observation_is_an_exact_event():
    t0 = kst(2026, 9, 26, 8, 0)
    _, events = run_all([obs("1", "왕십리", "ARRIVE", "용문", t0)], include_initial=True)
    assert [(e.station, e.event_type, e.inferred, e.uncertainty_seconds) for e in events] == [
        ("왕십리", EventType.ARRIVAL, False, 0.0)
    ]


def test_full_trip_every_station_arrival_and_departure():
    stations = ["용산", "이촌", "서빙고", "한남", "옥수", "응봉", "왕십리", "청량리"]
    tr, events = run_all(simulate_trip("5123", stations, kst(2026, 9, 25, 8, 0), 120, 30, poll=10))
    arrivals = [e.station for e in events if e.event_type is EventType.ARRIVAL]
    departures = [e.station for e in events if e.event_type is EventType.DEPARTURE]
    assert arrivals == stations[1:]          # first arrival happened before tracking started
    assert departures == stations[:-1]
    assert not any(e.inferred for e in events)
    run = tr.runs["2026-09-25:5123"]
    assert run.direction == 1 and run.terminated

    segments, dwells = derive_runs(events)
    assert len(segments) == len(stations) - 1
    assert len(dwells) == len(stations) - 2  # origin dwell start unknown, terminal has no departure
    for s in segments:
        assert abs(s.travel_seconds - 120) <= 10 and s.usable
    for d in dwells:
        assert abs(d.dwell_seconds - 30) <= 10 and d.usable


def test_reverse_direction_toward_munsan():
    stations = ["홍대입구", "가좌", "디지털미디어시티", "수색"]
    tr, events = run_all(simulate_trip("5200", stations, kst(2026, 9, 25, 9, 0), 100, 25, terminal="문산"))
    assert tr.runs["2026-09-25:5200"].direction == -1
    assert [e.station for e in events if e.event_type is EventType.ARRIVAL] == stations[1:]


def test_prev_departed_status_equals_departure_of_previous_station():
    t0 = kst(2026, 9, 25, 8, 0)
    tr, events = run_all([
        obs("1", "왕십리", "ARRIVE", "용문", t0),
        obs("1", "청량리", "PREV_DEPARTED", "용문", t0 + timedelta(seconds=40)),
    ])
    assert [(e.station, e.event_type) for e in events] == [("왕십리", EventType.DEPARTURE)]
    assert events[0].next_station == "청량리"


def test_skipped_milestones_are_inferred():
    t0 = kst(2026, 9, 25, 8, 0)
    _, events = run_all([
        obs("1", "왕십리", "ARRIVE", "용문", t0),
        obs("1", "청량리", "ARRIVE", "용문", t0 + timedelta(seconds=60)),
    ])
    kinds = [(e.station, e.event_type, e.inferred) for e in events]
    assert kinds == [
        ("왕십리", EventType.DEPARTURE, True),
        ("청량리", EventType.APPROACH, True),
        ("청량리", EventType.ARRIVAL, False),
    ]
    segments, _ = derive_runs(events)
    assert len(segments) == 1 and not segments[0].usable


def test_large_poll_gap_emits_no_events():
    t0 = kst(2026, 9, 25, 8, 0)
    tr, events = run_all([
        obs("1", "왕십리", "ARRIVE", "용문", t0),
        obs("1", "상봉", "ARRIVE", "용문", t0 + timedelta(minutes=10)),
    ])
    assert events == []
    assert tr.runs["2026-09-25:1"].gaps == 1


def test_gap_never_pairs_non_adjacent_stations():
    t0 = kst(2026, 9, 25, 8, 0)
    _, events = run_all([
        obs("1", "왕십리", "ARRIVE", "용문", t0),
        obs("1", "왕십리", "DEPART", "용문", t0 + timedelta(seconds=10)),
        obs("1", "회기", "ARRIVE", "용문", t0 + timedelta(minutes=10)),   # gap
        obs("1", "회기", "DEPART", "용문", t0 + timedelta(minutes=10, seconds=10)),
        obs("1", "중랑", "ARRIVE", "용문", t0 + timedelta(minutes=11, seconds=40)),
    ])
    segments, _ = derive_runs(events)
    assert [(s.from_station, s.to_station) for s in segments] == [("회기", "중랑")]


def test_regression_and_duplicates_ignored():
    t0 = kst(2026, 9, 25, 8, 0)
    tr, events = run_all([
        obs("1", "왕십리", "DEPART", "용문", t0),
        obs("1", "왕십리", "DEPART", "용문", t0),                       # duplicate
        obs("1", "왕십리", "ARRIVE", "용문", t0 + timedelta(seconds=10)),  # regression
    ])
    assert events == []
    run = tr.runs["2026-09-25:1"]
    assert run.regressions == 1
    assert tr.stats.stale_or_duplicate == 1


def test_unknown_station_is_counted_not_guessed():
    tr, events = run_all([obs("1", "임진강", "ARRIVE", "도라산", kst(2026, 9, 25, 8, 0))])
    assert events == [] and tr.stats.unresolved_names == {"임진강": 1, "도라산": 1}


def test_seoul_branch_run():
    stations = ["서울역", "신촌", "가좌", "디지털미디어시티"]
    tr, events = run_all(simulate_trip("7000", stations, kst(2026, 9, 25, 10, 0), 150, 30, terminal="문산"))
    run = tr.runs["2026-09-25:7000"]
    assert run.pattern.pattern_id == "SEOUL_BRANCH" and run.direction == 1
    segments, _ = derive_runs(events)
    assert [(s.from_station, s.to_station) for s in segments] == list(zip(stations, stations[1:]))


def test_link_spans_unreported_stations():
    from gjeta.tracking.derive import derive_links

    t0 = kst(2026, 9, 26, 8, 35)
    from gjeta.sources.seoul_realtime import parse_position_record
    from helpers import record

    def polled(station, status, changed_at, polled_at):
        # real API: recptnDt = time of the state change, repeated on every poll
        return parse_position_record(record("1", station, status, "문산", changed_at), observed_at=polled_at)

    stream = []
    for sec in range(0, 400, 15):   # stays "행신 arrived" while passing 능곡·대곡
        stream.append(polled("행신", "ARRIVE", t0, t0 + timedelta(seconds=sec)))
    stream.append(polled("곡산", "APPROACH", t0 + timedelta(seconds=400), t0 + timedelta(seconds=405)))
    stream.append(polled("곡산", "ARRIVE", t0 + timedelta(seconds=440), t0 + timedelta(seconds=450)))
    _, events = run_all(stream, include_initial=True)
    links = [(iv.a, iv.b, iv.seconds, iv.usable) for iv in derive_links(events) if iv.kind == "LINK"]
    assert ("행신", "곡산", 440.0, True) in links
