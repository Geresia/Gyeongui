"""Replays 8 real realtimePosition responses (경의중앙선, 2026-09-26 08:00 KST, 15 s apart).

Source: 서울특별시 공공데이터 (서울 열린데이터광장). The API key is masked in the fixture.
"""

import json
from pathlib import Path

from gjeta.models import EventType
from gjeta.sources.seoul_realtime import RawResponse, parse_position_response
from gjeta.stations import load_default_network
from gjeta.tracking.derive import derive_links
from gjeta.tracking.tracker import TrainTracker

FIXTURE = Path(__file__).parent / "fixtures" / "real_positions_20260926_0800.json"


def _replay():
    raws = [RawResponse.from_dict(d) for d in json.loads(FIXTURE.read_text(encoding="utf-8"))]
    tracker = TrainTracker(load_default_network())
    events = [e for r in raws for o in parse_position_response(r) for e in tracker.ingest(o)]
    return raws, tracker, events


def test_every_station_name_resolves_including_outside_seoul():
    raws, tracker, _ = _replay()
    names = {o.station_name for r in raws for o in parse_position_response(r)}
    assert {"문산", "구리", "덕소", "양평", "한국항공대"} <= names  # stations outside Seoul are provided
    assert tracker.stats.unresolved_names == {}


def test_observed_events_are_timed_exactly_at_recptnDt():
    _, _, events = _replay()
    observed = [e for e in events if not e.inferred]
    assert observed and all(e.uncertainty_seconds == 0 for e in observed)
    # departures are never reported directly in this sample, only inferred
    assert all(e.inferred for e in events if e.event_type is EventType.DEPARTURE)


def test_approach_to_arrival_intervals():
    _, _, events = _replay()
    approach = {iv.a: iv.seconds for iv in derive_links(events) if iv.kind == "APPROACH" and iv.usable}
    assert approach["서빙고"] == 42 and approach["용산"] == 99
