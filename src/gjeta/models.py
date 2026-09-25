"""Core data records for Phase 1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class ObsStatus(str, Enum):
    """Normalized trainSttus (documented: 0 진입, 1 도착, 2 출발, 3 전역출발)."""

    APPROACHING = "APPROACHING"
    ARRIVED = "ARRIVED"
    DEPARTED = "DEPARTED"
    PREV_DEPARTED = "PREV_DEPARTED"
    UNKNOWN = "UNKNOWN"


class EventType(str, Enum):
    APPROACH = "APPROACH"
    ARRIVAL = "ARRIVAL"
    DEPARTURE = "DEPARTURE"


@dataclass(frozen=True)
class PositionObservation:
    observed_at: datetime                 # when our collector received the response
    source_generated_at: datetime | None  # recptnDt; meaning UNKNOWN (DESIGN U2)
    service_date: date
    train_no: str
    station_name: str | None              # raw statnNm
    terminal_name: str | None             # raw statnTnm
    api_station_id: str | None
    updn_line: str | None
    status_code: str | None               # raw trainSttus
    status: ObsStatus
    express_code: str | None              # raw directAt
    is_last_train: bool | None
    raw: dict[str, Any] = field(repr=False, compare=False)

    @property
    def train_run_id(self) -> str:
        return f"{self.service_date.isoformat()}:{self.train_no}"

    @property
    def is_express(self) -> bool | None:
        if self.express_code is None:
            return None
        return self.express_code != "0"

    @property
    def event_time(self) -> datetime:
        """Best available time for this observation's state."""
        return self.source_generated_at or self.observed_at


@dataclass(frozen=True)
class StationEvent:
    train_run_id: str
    station: str
    event_type: EventType
    time_lower: datetime
    time_upper: datetime
    inferred: bool
    step: int                 # position along the run's direction (0 = first station of pattern)
    leg: int                  # increments when tracking restarts (pattern/direction change)
    next_station: str | None  # station at step + 1, None at the end of the pattern

    @property
    def estimated_time(self) -> datetime:
        return self.time_lower + (self.time_upper - self.time_lower) / 2

    @property
    def uncertainty_seconds(self) -> float:
        return (self.time_upper - self.time_lower).total_seconds() / 2


@dataclass(frozen=True)
class SegmentRun:
    train_run_id: str
    from_station: str
    to_station: str
    departure_time: datetime
    arrival_time: datetime
    travel_seconds: float
    uncertainty_seconds: float
    usable: bool


@dataclass(frozen=True)
class StationDwell:
    train_run_id: str
    station: str
    next_station: str
    arrival_time: datetime
    departure_time: datetime
    dwell_seconds: float
    uncertainty_seconds: float
    usable: bool


@dataclass(frozen=True)
class TravelInterval:
    """Generic timed interval between two milestones.

    kind LINK:     ARRIVAL(a) -> ARRIVAL(b), b the next station (dwell at a + run a->b)
    kind APPROACH: APPROACH(a) -> ARRIVAL(a), with b == a
    """

    kind: str
    train_run_id: str
    a: str
    b: str
    start: datetime
    end: datetime
    seconds: float
    uncertainty_seconds: float
    usable: bool
