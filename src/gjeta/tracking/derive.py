"""Station events -> segment runs and station dwells."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import EventType, SegmentRun, StationDwell, StationEvent, TravelInterval

# Combined uncertainty above this (seconds) keeps the record but marks it unusable
# for statistics. With 15 s polling each event is +-7.5 s, so a pair is ~15 s.
DEFAULT_MAX_UNCERTAINTY_SECONDS = 20.0


def derive_runs(
    events: Iterable[StationEvent],
    max_uncertainty: float = DEFAULT_MAX_UNCERTAINTY_SECONDS,
) -> tuple[list[SegmentRun], list[StationDwell]]:
    """Pair events of the same run leg by step: dwell = DEP(k) - ARR(k),
    segment = ARR(k+1) - DEP(k). Non-adjacent steps are never paired."""
    index: dict[tuple[str, int, int, EventType], StationEvent] = {}
    for e in events:
        index.setdefault((e.train_run_id, e.leg, e.step, e.event_type), e)

    segments: list[SegmentRun] = []
    dwells: list[StationDwell] = []
    for (run_id, leg, step, etype), dep in index.items():
        if etype is not EventType.DEPARTURE:
            continue
        arr = index.get((run_id, leg, step, EventType.ARRIVAL))
        if arr is not None and dep.next_station is not None and dep.estimated_time >= arr.estimated_time:
            unc = arr.uncertainty_seconds + dep.uncertainty_seconds
            dwells.append(
                StationDwell(
                    train_run_id=run_id,
                    station=dep.station,
                    next_station=dep.next_station,
                    arrival_time=arr.estimated_time,
                    departure_time=dep.estimated_time,
                    dwell_seconds=(dep.estimated_time - arr.estimated_time).total_seconds(),
                    uncertainty_seconds=unc,
                    usable=not (arr.inferred or dep.inferred) and unc <= max_uncertainty,
                )
            )
        nxt = index.get((run_id, leg, step + 1, EventType.ARRIVAL))
        if nxt is not None and nxt.estimated_time >= dep.estimated_time:
            unc = dep.uncertainty_seconds + nxt.uncertainty_seconds
            segments.append(
                SegmentRun(
                    train_run_id=run_id,
                    from_station=dep.station,
                    to_station=nxt.station,
                    departure_time=dep.estimated_time,
                    arrival_time=nxt.estimated_time,
                    travel_seconds=(nxt.estimated_time - dep.estimated_time).total_seconds(),
                    uncertainty_seconds=unc,
                    usable=not (dep.inferred or nxt.inferred) and unc <= max_uncertainty,
                )
            )
    return segments, dwells


def derive_links(
    events: Iterable[StationEvent],
    max_uncertainty: float = DEFAULT_MAX_UNCERTAINTY_SECONDS,
) -> list[TravelInterval]:
    """Arrival-to-arrival links and approach-to-arrival intervals.

    The real-time API rarely reports departures (DESIGN §2.1), so these are the
    primary statistics; segment/dwell stay for when departures are observed.
    """
    index: dict[tuple[str, int, int, EventType], StationEvent] = {}
    for e in events:
        index.setdefault((e.train_run_id, e.leg, e.step, e.event_type), e)

    out: list[TravelInterval] = []
    for (run_id, leg, step, etype), end in index.items():
        if etype is not EventType.ARRIVAL:
            continue
        pairs = (
            ("LINK", index.get((run_id, leg, step - 1, EventType.ARRIVAL))),
            ("APPROACH", index.get((run_id, leg, step, EventType.APPROACH))),
        )
        for kind, start in pairs:
            if start is None or end.estimated_time < start.estimated_time:
                continue
            unc = start.uncertainty_seconds + end.uncertainty_seconds
            out.append(
                TravelInterval(
                    kind=kind,
                    train_run_id=run_id,
                    a=start.station,
                    b=end.station,
                    start=start.estimated_time,
                    end=end.estimated_time,
                    seconds=(end.estimated_time - start.estimated_time).total_seconds(),
                    uncertainty_seconds=unc,
                    usable=not (start.inferred or end.inferred) and unc <= max_uncertainty,
                )
            )
    return out
