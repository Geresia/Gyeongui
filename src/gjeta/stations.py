"""Station names, route patterns, and direction logic.

The seed CSV is built from the public line map. Station names as returned by the
Seoul API are NOT verified yet (DESIGN U7); unknown names resolve to None instead of
being guessed.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

_PAREN = re.compile(r"\(.*?\)")

# Known spelling differences -> canonical seed name. Extend from real payloads.
DEFAULT_ALIASES: dict[str, str] = {
    "서울": "서울역",
    "DMC": "디지털미디어시티",
}


def normalize_name(name: str) -> str:
    """'신촌(경의중앙선)' -> '신촌', ' 왕십리역 ' -> '왕십리'. '서울역' keeps its suffix."""
    text = _PAREN.sub("", name).strip().replace(" ", "")
    if text.endswith("역") and text != "서울역" and len(text) > 2:
        text = text[:-1]
    return text


@dataclass(frozen=True)
class RoutePattern:
    pattern_id: str
    stations: tuple[str, ...]

    def index(self, station: str) -> int | None:
        try:
            return self.stations.index(station)
        except ValueError:
            return None

    def __contains__(self, station: object) -> bool:
        return station in self.stations


class Network:
    def __init__(self, patterns: list[RoutePattern], aliases: dict[str, str] | None = None):
        if not patterns:
            raise ValueError("at least one route pattern is required")
        self.patterns = {p.pattern_id: p for p in patterns}
        self.default_pattern_id = patterns[0].pattern_id
        known = {s for p in patterns for s in p.stations}
        self._aliases = {normalize_name(s): s for s in known}
        for alias, canonical in (aliases or {}).items():
            if canonical in known:
                self._aliases[normalize_name(alias)] = canonical

    def resolve(self, name: str | None) -> str | None:
        if not name:
            return None
        return self._aliases.get(normalize_name(name))

    def choose_pattern(
        self, station: str, terminal: str, current_pattern_id: str | None = None
    ) -> RoutePattern | None:
        """Pick a pattern that contains both the current station and the terminal.

        Prefers the pattern the run already uses so ordinals stay consistent.
        """
        candidates = [p for p in self.patterns.values() if station in p and terminal in p]
        if not candidates:
            return None
        for p in candidates:
            if p.pattern_id == current_pattern_id:
                return p
        for p in candidates:
            if p.pattern_id == self.default_pattern_id:
                return p
        return candidates[0]


def direction_toward(pattern: RoutePattern, station: str, terminal: str) -> int | None:
    """+1 if the terminal lies later in the pattern, -1 if earlier, None if same/unknown."""
    i, t = pattern.index(station), pattern.index(terminal)
    if i is None or t is None or i == t:
        return None
    return 1 if t > i else -1


def neighbor(pattern: RoutePattern, station: str, direction: int, steps: int = 1) -> str | None:
    i = pattern.index(station)
    if i is None:
        return None
    j = i + direction * steps
    if 0 <= j < len(pattern.stations):
        return pattern.stations[j]
    return None


@lru_cache(maxsize=1)
def load_default_network() -> Network:
    rows: dict[str, list[tuple[int, str]]] = {}
    text = resources.files("gjeta").joinpath("data/gyeongui_jungang_stations.csv").read_text("utf-8")
    for row in csv.DictReader(text.splitlines()):
        rows.setdefault(row["pattern_id"], []).append((int(row["seq"]), row["station_name"]))
    patterns = [
        RoutePattern(pid, tuple(name for _, name in sorted(items))) for pid, items in rows.items()
    ]
    return Network(patterns, DEFAULT_ALIASES)
