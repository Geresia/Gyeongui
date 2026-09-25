"""Station events -> segment runs and station dwells."""

from __future__ import annotations

from collections.abc import Iterable

from ..models import EventType, SegmentRun, StationDwell, StationEvent

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
