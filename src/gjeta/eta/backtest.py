"""Leakage-free backtest of the baseline.

Stats are built only from service days strictly before the test day; predictions are
made while replaying the test day's observations in time order and are scored against
the ARRIVAL events the tracker later produces for the same day.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from ..models import EventType, PositionObservation, StationEvent
from ..stations import Network
from ..tracking.derive import derive_links, derive_runs
from ..tracking.tracker import TrainTracker
from .baseline import EtaResult, baseline_eta, position_from_run
from .evaluation import Evaluation, evaluate
from .stats import TravelStats

DEFAULT_PREDICT_EVERY_SECONDS = 60.0
DEFAULT_MAX_TARGETS = 10


def build_stats(observations: Iterable[PositionObservation], network: Network, min_samples: int) -> TravelStats:
    tracker = TrainTracker(network)
    events: list[StationEvent] = []
    for obs in sorted(observations, key=lambda o: o.event_time):
        events.extend(tracker.ingest(obs))
    segments, dwells = derive_runs(events)
    stats = TravelStats(min_samples=min_samples)
    stats.add_segments(segments)
    stats.add_dwells(dwells)
    stats.add_intervals(derive_links(events))
    return stats


def backtest_day(
    test_obs: list[PositionObservation],
    stats: TravelStats,
    network: Network,
    predict_every: float = DEFAULT_PREDICT_EVERY_SECONDS,
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> list[Evaluation]:
    tracker = TrainTracker(network)
    predictions: list[EtaResult] = []
    arrivals: dict[tuple[str, str], StationEvent] = {}
    last_predicted: dict[str, datetime] = {}

    for obs in sorted(test_obs, key=lambda o: o.event_time):
        for e in tracker.ingest(obs):
            if e.event_type is EventType.ARRIVAL and not e.inferred:
                arrivals.setdefault((e.train_run_id, e.station), e)
        run = tracker.runs.get(obs.train_run_id)
        if run is None or run.terminated or run.is_express:
            continue  # MVP: express trains are collected but not predicted (DESIGN U5)
        now = obs.event_time
        prev = last_predicted.get(run.train_run_id)
        if prev is not None and (now - prev).total_seconds() < predict_every:
            continue
        pos = position_from_run(run)
        if pos is None:
            continue
        last_predicted[run.train_run_id] = now
        for target in pos.path[:max_targets]:
            r = baseline_eta(pos, target, now, stats)
            if r is not None:
                predictions.append(r)

    out = []
    for p in predictions:
        actual = arrivals.get((p.train_run_id, p.target))
        if actual is not None and actual.estimated_time >= p.predicted_at:
            out.append(evaluate(p, actual))
    return out


def split_by_day(observations: Iterable[PositionObservation]) -> dict[date, list[PositionObservation]]:
    days: dict[date, list[PositionObservation]] = {}
    for o in observations:
        days.setdefault(o.service_date, []).append(o)
    return days
