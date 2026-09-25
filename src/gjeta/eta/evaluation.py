"""Prediction vs actual arrival (SPEC §43-44)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..models import StationEvent
from .baseline import EtaResult
from .stats import quantile

HORIZON_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-5m", 0, 300),
    ("5-10m", 300, 600),
    ("10-20m", 600, 1200),
    ("20-40m", 1200, 2400),
    ("40m+", 2400, float("inf")),
)


@dataclass(frozen=True)
class Evaluation:
    prediction: EtaResult
    actual_seconds: float           # actual arrival - predicted_at
    actual_uncertainty_seconds: float

    @property
    def signed_error(self) -> float:
        return self.prediction.eta_seconds - self.actual_seconds

    @property
    def abs_error(self) -> float:
        return abs(self.signed_error)

    @property
    def horizon_seconds(self) -> float:
        # Bucket by the *predicted* ETA (what the user saw). Bucketing by the actual
        # travel time would push slow trains into long buckets and bias them.
        return self.prediction.eta_seconds

    @property
    def within_range(self) -> bool:
        return self.prediction.lower_seconds <= self.actual_seconds <= self.prediction.upper_seconds


def evaluate(prediction: EtaResult, actual_arrival: StationEvent) -> Evaluation:
    return Evaluation(
        prediction=prediction,
        actual_seconds=(actual_arrival.estimated_time - prediction.predicted_at).total_seconds(),
        actual_uncertainty_seconds=actual_arrival.uncertainty_seconds,
    )


def summarize(evals: Iterable[Evaluation]) -> list[dict[str, object]]:
    buckets: dict[str, list[Evaluation]] = {name: [] for name, _, _ in HORIZON_BUCKETS}
    for e in evals:
        for name, lo, hi in HORIZON_BUCKETS:
            if lo <= e.horizon_seconds < hi:
                buckets[name].append(e)
                break
    rows = []
    for name, items in buckets.items():
        if not items:
            rows.append({"horizon": name, "n": 0})
            continue
        errs = sorted(e.abs_error for e in items)
        rows.append(
            {
                "horizon": name,
                "n": len(items),
                "mae": sum(errs) / len(errs),
                "median_ae": quantile(errs, 0.5),
                "p80_ae": quantile(errs, 0.8),
                "p95_ae": quantile(errs, 0.95),
                "bias": sum(e.signed_error for e in items) / len(items),
                "coverage": sum(e.within_range for e in items) / len(items),
                "actual_uncertainty_mean": sum(e.actual_uncertainty_seconds for e in items) / len(items),
            }
        )
    return rows
