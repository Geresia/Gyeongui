"""Historical travel/dwell samples with bucket fallback (DESIGN §6.3)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from ..models import SegmentRun, StationDwell
from ..timeutil import day_type, time_band

ANY = "*"
DEFAULT_MIN_SAMPLES = 10


class Kind(str, Enum):
    SEGMENT = "SEGMENT"   # key (from, to)
    DWELL = "DWELL"       # key (station, next_station)


class Level(str, Enum):
    L0 = "L0"             # pair + dayType + timeBand
    L1 = "L1"             # pair + dayType
    L2 = "L2"             # pair only
    MISSING = "MISSING"


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile of an already sorted, non-empty sequence."""
    if not sorted_values:
        raise ValueError("empty sample")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


@dataclass(frozen=True)
class Lookup:
    samples: tuple[float, ...]  # sorted; empty when MISSING
    level: Level


class TravelStats:
    """In-memory sample store. Phase 1 keeps raw samples because conditional
    remaining time needs them; the DB table `travel_stats` holds the quantile summary.
    TODO: cap per-key samples (e.g. most recent N weeks) once data grows."""

    def __init__(self, min_samples: int = DEFAULT_MIN_SAMPLES, holidays: frozenset[date] = frozenset()):
        self.min_samples = min_samples
        self.holidays = holidays
        self._samples: dict[tuple[str, str, str, str, str], list[float]] = {}
        self._frozen: dict[tuple[str, str, str, str, str], tuple[float, ...]] = {}

    def _keys(self, kind: Kind, a: str, b: str, when: datetime) -> list[tuple[Level, tuple[str, str, str, str, str]]]:
        dt = day_type(when, self.holidays).value
        tb = time_band(when).value
        return [
            (Level.L0, (kind.value, a, b, dt, tb)),
            (Level.L1, (kind.value, a, b, dt, ANY)),
            (Level.L2, (kind.value, a, b, ANY, ANY)),
        ]

    def add(self, kind: Kind, a: str, b: str, when: datetime, seconds: float) -> None:
        for _, key in self._keys(kind, a, b, when):
            self._samples.setdefault(key, []).append(seconds)
            self._frozen.pop(key, None)

    def add_segments(self, runs: Iterable[SegmentRun]) -> None:
        for r in runs:
            if r.usable:
                self.add(Kind.SEGMENT, r.from_station, r.to_station, r.departure_time, r.travel_seconds)

    def add_dwells(self, dwells: Iterable[StationDwell]) -> None:
        for d in dwells:
            if d.usable:
                self.add(Kind.DWELL, d.station, d.next_station, d.arrival_time, d.dwell_seconds)

    def lookup(self, kind: Kind, a: str, b: str, when: datetime) -> Lookup:
        for level, key in self._keys(kind, a, b, when):
            values = self._samples.get(key)
            if values and len(values) >= self.min_samples:
                frozen = self._frozen.get(key)
                if frozen is None:
                    frozen = tuple(sorted(values))
                    self._frozen[key] = frozen
                return Lookup(frozen, level)
        return Lookup((), Level.MISSING)

    def summary_rows(self) -> list[dict[str, object]]:
        """Rows shaped like the `travel_stats` table."""
        rows = []
        for (kind, a, b, dt, tb), values in sorted(self._samples.items()):
            v = sorted(values)
            rows.append(
                {
                    "kind": kind, "from": a, "to": b, "day_type": dt, "time_band": tb, "n": len(v),
                    "p20": quantile(v, 0.2), "p50": quantile(v, 0.5),
                    "p80": quantile(v, 0.8), "p95": quantile(v, 0.95),
                }
            )
        return rows
