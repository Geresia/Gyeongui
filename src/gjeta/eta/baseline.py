"""Baseline ETA (M2): current position + historical segment/dwell medians.

    ETA = CondRemain(current segment or dwell, elapsed)
        + Σ segment(j -> j+1)  for the remaining segments
        + Σ dwell(j)           for intermediate stations (not the target)

Every future model must beat this one on the same evaluation (SPEC §60).
See DESIGN §7 for the reasoning behind each choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..models import EventType
from ..tracking.tracker import RunState
from .stats import Kind, Level, Lookup, TravelStats, quantile

MODEL_VERSION = "baseline-v0"

LOWER_Q, MID_Q, UPPER_Q = 0.2, 0.5, 0.8

LEVEL_WEIGHT = {Level.L0: 1.0, Level.L1: 0.95, Level.L2: 0.85, Level.MISSING: 0.5}
FRESH_SECONDS = 30.0
STALE_SECONDS = 180.0
ABNORMAL_FACTOR = 0.3


class Motion(str, Enum):
    DWELLING = "DWELLING"   # arrived at `station`, not yet departed
    RUNNING = "RUNNING"     # departed `station`, heading to path[0]


@dataclass(frozen=True)
class TrainPosition:
    train_run_id: str
    motion: Motion
    station: str                    # DWELLING: current station; RUNNING: last departed station
    path: tuple[str, ...]           # stations ahead in travel order, starting with the next one
    since: datetime | None          # arrival (DWELLING) / departure (RUNNING) time; None if unseen
    last_seen: datetime             # latest observation of this train


@dataclass(frozen=True)
class Component:
    kind: Kind
    a: str
    b: str
    level: Level
    n: int
    p20: float
    p50: float
    p80: float
    conditional: bool = False


@dataclass(frozen=True)
class EtaResult:
    train_run_id: str
    target: str
    predicted_at: datetime
    eta_seconds: float
    lower_seconds: float
    upper_seconds: float
    confidence: float
    abnormal: bool
    components: tuple[Component, ...] = field(repr=False)
    model_version: str = MODEL_VERSION

    @property
    def predicted_arrival(self) -> datetime:
        return self.predicted_at + timedelta(seconds=self.eta_seconds)


def position_from_run(run: RunState) -> TrainPosition | None:
    """Translate tracker state into a motion state the ETA can use."""
    k, phase = divmod(run.ordinal, 3)
    ahead = _path_after(run, k)
    if phase == 1:  # ARRIVAL(k)
        return TrainPosition(run.train_run_id, Motion.DWELLING, run._station_at(k), ahead,
                             run.milestone_times.get(run.ordinal), run.last_seen)
    if phase == 2:  # DEPARTURE(k)
        if not ahead:
            return None
        return TrainPosition(run.train_run_id, Motion.RUNNING, run._station_at(k), ahead,
                             run.milestone_times.get(run.ordinal), run.last_seen)
    # APPROACH(k) is a hint only: the train is still in segment (k-1 -> k).
    if k == 0:
        return None
    return TrainPosition(run.train_run_id, Motion.RUNNING, run._station_at(k - 1), _path_after(run, k - 1),
                         run.milestone_times.get(3 * (k - 1) + 2), run.last_seen)


def _path_after(run: RunState, k: int) -> tuple[str, ...]:
    out = []
    j = k + 1
    while (s := run._station_at(j)) is not None:
        out.append(s)
        if s == run.terminal:
            break
        j += 1
    return tuple(out)


def _component(kind: Kind, a: str, b: str, lk: Lookup, elapsed: float | None) -> tuple[Component, bool]:
    """Returns (component, abnormal). With `elapsed`, uses conditional remaining time."""
    if lk.level is Level.MISSING:
        return Component(kind, a, b, lk.level, 0, 0.0, 0.0, 0.0), False
    samples = lk.samples
    if elapsed is not None and elapsed > 0:
        remaining = [x - elapsed for x in samples if x > elapsed]
        if not remaining:
            # Longer than every observed run: abnormal stop / slow running (SPEC §26).
            return Component(kind, a, b, lk.level, len(samples), 0.0, 0.0, 0.0, conditional=True), True
        return Component(kind, a, b, lk.level, len(remaining), quantile(remaining, LOWER_Q),
                         quantile(remaining, MID_Q), quantile(remaining, UPPER_Q), conditional=True), False
    return Component(kind, a, b, lk.level, len(samples), quantile(samples, LOWER_Q),
                     quantile(samples, MID_Q), quantile(samples, UPPER_Q)), False


def _staleness_factor(age_seconds: float) -> float:
    if age_seconds <= FRESH_SECONDS:
        return 1.0
    if age_seconds >= STALE_SECONDS:
        return 0.5
    return 1.0 - 0.5 * (age_seconds - FRESH_SECONDS) / (STALE_SECONDS - FRESH_SECONDS)


def baseline_eta(pos: TrainPosition, target: str, now: datetime, stats: TravelStats) -> EtaResult | None:
    """ETA of `pos`'s train arriving at `target`. None if target is not ahead."""
    if target not in pos.path:
        return None
    elapsed = (now - pos.since).total_seconds() if pos.since is not None else None
    parts: list[Component] = []
    abnormal = False

    # 1) what is left of the current dwell / segment
    first = pos.path[0]
    if pos.motion is Motion.DWELLING:
        c, ab = _component(Kind.DWELL, pos.station, first, stats.lookup(Kind.DWELL, pos.station, first, now), elapsed)
        parts.append(c)
        abnormal |= ab
        c, _ = _component(Kind.SEGMENT, pos.station, first, stats.lookup(Kind.SEGMENT, pos.station, first, now), None)
        parts.append(c)
    else:
        c, ab = _component(Kind.SEGMENT, pos.station, first, stats.lookup(Kind.SEGMENT, pos.station, first, now), elapsed)
        parts.append(c)
        abnormal |= ab

    # 2) remaining dwells + segments up to the target. Stats are looked up at `now`;
    # for long horizons the time band may shift - acceptable for the baseline.
    stops = pos.path[: pos.path.index(target) + 1]
    for a, b in zip(stops, stops[1:]):
        for kind in (Kind.DWELL, Kind.SEGMENT):
            c, _ = _component(kind, a, b, stats.lookup(kind, a, b, now), None)
            parts.append(c)

    # Comonotonic sum: quantiles add up (conservative, DESIGN §7).
    eta = sum(p.p50 for p in parts)
    lower = sum(p.p20 for p in parts)
    upper = sum(p.p80 for p in parts)

    confidence = 1.0
    for p in parts:
        confidence *= LEVEL_WEIGHT[p.level] ** (1 / len(parts))  # geometric mean of weights
    confidence *= _staleness_factor((now - pos.last_seen).total_seconds())
    if abnormal:
        confidence *= ABNORMAL_FACTOR
    if elapsed is None:
        confidence *= 0.8  # we do not know when the current phase started

    return EtaResult(
        train_run_id=pos.train_run_id,
        target=target,
        predicted_at=now,
        eta_seconds=eta,
        lower_seconds=lower,
        upper_seconds=upper,
        confidence=round(confidence, 3),
        abnormal=abnormal,
        components=tuple(parts),
    )


def format_minutes(result: EtaResult) -> str:
    """User-facing text per SPEC §28: no seconds, always a range."""
    mid = max(0, round(result.eta_seconds / 60))
    lo = max(0, int(result.lower_seconds // 60))
    hi = max(mid, -(-int(result.upper_seconds) // 60))
    if mid == 0:
        return "곧 도착"
    return f"약 {mid}분 후 (예상 범위 {lo}~{hi}분)"
