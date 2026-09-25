"""Train tracking: position observations -> station events.

Progress-ordinal model (DESIGN §6.2). For the k-th station along the run's direction:

    APPROACH(k)  = 3k + 0   (trainSttus 0, 진입)
    ARRIVAL(k)   = 3k + 1   (trainSttus 1, 도착)
    DEPARTURE(k) = 3k + 2   (trainSttus 2, 출발 / trainSttus 3 at k+1, 전역출발)

When a run's ordinal increases, every milestone passed since the previous observation
becomes an event bounded by [previous observation time, current observation time].
Milestones that were skipped between polls are flagged `inferred`.

Pure logic, no I/O: the collector (live) and replay (batch) share this code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..models import EventType, ObsStatus, PositionObservation, StationEvent
from ..stations import Network, RoutePattern, direction_toward

_PHASES = (EventType.APPROACH, EventType.ARRIVAL, EventType.DEPARTURE)

# A poll gap larger than this means we lost track (collector down, API outage);
# milestones inside it are not turned into events so they cannot pollute stats.
DEFAULT_MAX_GAP_SECONDS = 180.0


@dataclass
class RunState:
    train_run_id: str
    train_no: str
    pattern: RoutePattern
    direction: int
    terminal: str
    ordinal: int
    last_time: datetime
    is_express: bool | None
    first_seen: datetime
    last_seen: datetime
    # estimated time of each milestone reached in the current leg (ordinal -> time)
    milestone_times: dict[int, datetime] = field(default_factory=dict)
    leg: int = 0
    gaps: int = 0
    regressions: int = 0
    terminated: bool = False

    def position(self) -> tuple[str, EventType]:
        """(station, last milestone type) the run is currently at."""
        k, phase = divmod(self.ordinal, 3)
        return self._station_at(k), _PHASES[phase]

    def _station_at(self, k: int) -> str | None:
        # k counts along the direction of travel.
        n = len(self.pattern.stations)
        if not 0 <= k < n:
            return None
        return self.pattern.stations[k if self.direction > 0 else n - 1 - k]

    def step_of(self, station: str) -> int | None:
        i = self.pattern.index(station)
        if i is None:
            return None
        return i if self.direction > 0 else len(self.pattern.stations) - 1 - i


@dataclass
class TrackerStats:
    observations: int = 0
    unresolved_station: int = 0
    unresolved_pattern: int = 0
    unknown_status: int = 0
    stale_or_duplicate: int = 0
    events: int = 0
    inferred_events: int = 0
    unresolved_names: dict[str, int] = field(default_factory=dict)


class TrainTracker:
    def __init__(self, network: Network, max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS):
        self.network = network
        self.max_gap_seconds = max_gap_seconds
        self.runs: dict[str, RunState] = {}
        self.stats = TrackerStats()

    def ingest(self, obs: PositionObservation) -> list[StationEvent]:
        self.stats.observations += 1
        if obs.status is ObsStatus.UNKNOWN:
            self.stats.unknown_status += 1
            return []
        station = self.network.resolve(obs.station_name)
        terminal = self.network.resolve(obs.terminal_name)
        if station is None or terminal is None:
            self.stats.unresolved_station += 1
            for name in (obs.station_name, obs.terminal_name):
                if name and self.network.resolve(name) is None:
                    self.stats.unresolved_names[name] = self.stats.unresolved_names.get(name, 0) + 1
            return []

        run = self.runs.get(obs.train_run_id)
        pattern = self.network.choose_pattern(station, terminal, run.pattern.pattern_id if run else None)
        if pattern is None:
            self.stats.unresolved_pattern += 1
            return []
        direction = direction_toward(pattern, station, terminal)
        if direction is None:
            # At the terminal itself: direction only known from earlier observations.
            if run is None or run.pattern.pattern_id != pattern.pattern_id:
                self.stats.unresolved_pattern += 1
                return []
            direction = run.direction

        t = obs.event_time
        if run is None or run.pattern.pattern_id != pattern.pattern_id or run.direction != direction:
            self._start_run(obs, pattern, direction, terminal, station, t, previous=run)
            return []

        run.last_seen = max(run.last_seen, t)
        new_ordinal = _ordinal(run, station, obs.status)
        if new_ordinal is None:
            self.stats.unresolved_pattern += 1
            return []
        if t <= run.last_time:
            self.stats.stale_or_duplicate += 1
            return []
        if new_ordinal < run.ordinal:
            run.regressions += 1
            return []
        if new_ordinal == run.ordinal:
            run.last_time = t
            return []

        gap = (t - run.last_time).total_seconds()
        events: list[StationEvent] = []
        if gap <= self.max_gap_seconds:
            for m in range(run.ordinal + 1, new_ordinal + 1):
                k, phase = divmod(m, 3)
                events.append(
                    StationEvent(
                        train_run_id=run.train_run_id,
                        station=run._station_at(k),
                        event_type=_PHASES[phase],
                        time_lower=run.last_time,
                        time_upper=t,
                        inferred=m != new_ordinal,
                        step=k,
                        leg=run.leg,
                        next_station=run._station_at(k + 1),
                    )
                )
            for e, m in zip(events, range(run.ordinal + 1, new_ordinal + 1)):
                run.milestone_times[m] = e.estimated_time
        else:
            run.gaps += 1
        run.ordinal = new_ordinal
        run.last_time = t
        if station == run.terminal and obs.status is ObsStatus.ARRIVED:
            run.terminated = True
        self.stats.events += len(events)
        self.stats.inferred_events += sum(e.inferred for e in events)
        return events

    def _start_run(
        self,
        obs: PositionObservation,
        pattern: RoutePattern,
        direction: int,
        terminal: str,
        station: str,
        t: datetime,
        previous: RunState | None,
    ) -> None:
        run = RunState(
            train_run_id=obs.train_run_id,
            train_no=obs.train_no,
            pattern=pattern,
            direction=direction,
            terminal=terminal,
            ordinal=0,
            last_time=t,
            is_express=obs.is_express,
            first_seen=previous.first_seen if previous else t,
            last_seen=t,
            leg=(previous.leg + 1) if previous else 0,
            gaps=(previous.gaps + 1) if previous else 0,
        )
        ordinal = _ordinal(run, station, obs.status)
        if ordinal is None:
            self.stats.unresolved_pattern += 1
            return
        run.ordinal = ordinal
        self.runs[obs.train_run_id] = run


def _ordinal(run: RunState, station: str, status: ObsStatus) -> int | None:
    k = run.step_of(station)
    if k is None:
        return None
    if status is ObsStatus.APPROACHING:
        return 3 * k
    if status is ObsStatus.ARRIVED:
        return 3 * k + 1
    if status is ObsStatus.DEPARTED:
        return 3 * k + 2
    if status is ObsStatus.PREV_DEPARTED:
        # "Previous station departed" == DEPARTURE of step k-1. At the first station
        # there is no previous one; treat as not yet departed.
        return 3 * (k - 1) + 2 if k > 0 else 3 * k
    return None
