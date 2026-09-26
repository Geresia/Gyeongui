from datetime import timedelta

from gjeta.eta.backtest import backtest_day, build_stats
from gjeta.eta.baseline import Motion, TrainPosition, baseline_eta, format_minutes, position_from_run
from gjeta.eta.evaluation import summarize
from gjeta.eta.stats import Kind, Level, TravelStats, quantile
from gjeta.stations import load_default_network
from gjeta.tracking.tracker import TrainTracker

from helpers import kst, obs, simulate_trip

NET = load_default_network()
NOW = kst(2026, 9, 25, 8, 30)


def make_stats(seg: dict[tuple[str, str], list[float]], dwell: dict[tuple[str, str], list[float]]) -> TravelStats:
    st = TravelStats(min_samples=3)
    for (a, b), xs in seg.items():
        for x in xs:
            st.add(Kind.SEGMENT, a, b, NOW, x)
    for (a, b), xs in dwell.items():
        for x in xs:
            st.add(Kind.DWELL, a, b, NOW, x)
    return st


def test_quantile():
    assert quantile([1, 2, 3, 4, 5], 0.5) == 3
    assert quantile([0, 10], 0.25) == 2.5


def test_lookup_fallback_levels():
    st = TravelStats(min_samples=3)
    for i in range(3):
        st.add(Kind.SEGMENT, "왕십리", "청량리", NOW, 200 + i)
    assert st.lookup(Kind.SEGMENT, "왕십리", "청량리", NOW).level is Level.L0
    # A different time band on the same weekday falls back to L1.
    assert st.lookup(Kind.SEGMENT, "왕십리", "청량리", NOW.replace(hour=13)).level is Level.L1
    # Saturday -> only the pair-level bucket has enough samples.
    assert st.lookup(Kind.SEGMENT, "왕십리", "청량리", NOW + timedelta(days=1)).level is Level.L2
    assert st.lookup(Kind.SEGMENT, "청량리", "회기", NOW).level is Level.MISSING


STATS = make_stats(
    seg={("왕십리", "청량리"): [200, 220, 240], ("청량리", "회기"): [100, 110, 120], ("회기", "중랑"): [90, 100, 110]},
    dwell={("청량리", "회기"): [30, 40, 50], ("회기", "중랑"): [20, 25, 30]},
)


def test_eta_running_sums_medians_without_target_dwell():
    pos = TrainPosition("r", Motion.RUNNING, "왕십리", ("청량리", "회기", "중랑"), since=NOW, last_seen=NOW)
    r = baseline_eta(pos, "회기", NOW, STATS)
    # segment 220 + dwell(청량리) 40 + segment 110; no dwell at the target
    assert r.eta_seconds == 370
    assert r.lower_seconds <= r.eta_seconds <= r.upper_seconds
    assert r.confidence == 1.0 and not r.abnormal


def test_eta_conditional_remaining_grows_with_elapsed_share():
    pos = TrainPosition("r", Motion.RUNNING, "왕십리", ("청량리",), since=NOW - timedelta(seconds=210), last_seen=NOW)
    r = baseline_eta(pos, "청량리", NOW, STATS)
    # only the 220 and 240 samples exceed 210 s -> remaining {10, 30}, median 20
    assert r.eta_seconds == 20


def test_eta_abnormal_when_longer_than_all_history():
    pos = TrainPosition("r", Motion.RUNNING, "왕십리", ("청량리",), since=NOW - timedelta(seconds=400), last_seen=NOW)
    r = baseline_eta(pos, "청량리", NOW, STATS)
    assert r.abnormal and r.confidence < 0.5


def test_eta_dwelling_and_missing_stats():
    pos = TrainPosition("r", Motion.DWELLING, "청량리", ("회기", "중랑", "상봉"), since=NOW, last_seen=NOW)
    assert baseline_eta(pos, "중랑", NOW, STATS).eta_seconds == 40 + 110 + 25 + 100
    missing = baseline_eta(pos, "상봉", NOW, STATS)
    assert any(c.level is Level.MISSING for c in missing.components)
    assert missing.confidence < 1.0
    assert baseline_eta(pos, "왕십리", NOW, STATS) is None  # behind the train


def test_stale_observation_lowers_confidence():
    pos = TrainPosition("r", Motion.RUNNING, "왕십리", ("청량리",), since=NOW, last_seen=NOW - timedelta(seconds=200))
    fresh = TrainPosition("r", Motion.RUNNING, "왕십리", ("청량리",), since=NOW, last_seen=NOW)
    assert baseline_eta(pos, "청량리", NOW, STATS).confidence < baseline_eta(fresh, "청량리", NOW, STATS).confidence


def test_format_minutes_never_shows_seconds():
    pos = TrainPosition("r", Motion.RUNNING, "왕십리", ("청량리", "회기", "중랑"), since=NOW, last_seen=NOW)
    assert format_minutes(baseline_eta(pos, "회기", NOW, STATS)) == "약 6분 후 (예상 범위 5~7분)"


def test_position_from_tracker_approach_state():
    t0 = kst(2026, 9, 25, 8, 0)
    tr = TrainTracker(NET)
    for o in [obs("1", "왕십리", "ARRIVE", "용문", t0),
              obs("1", "청량리", "APPROACH", "용문", t0 + timedelta(seconds=120))]:
        tr.ingest(o)
    pos = position_from_run(tr.runs["2026-09-25:1"])
    assert pos.motion is Motion.APPROACHING and pos.station == "청량리"
    assert pos.path[0] == "회기" and pos.path[-1] == "용문"
    assert pos.since == t0 + timedelta(seconds=120)  # exact: recptnDt of the change


LINK_STATS = make_stats(
    seg={},
    dwell={},
)
for (a, b), xs in {("왕십리", "청량리"): [150, 160, 170], ("청량리", "회기"): [130, 140, 150]}.items():
    for x in xs:
        LINK_STATS.add(Kind.LINK, a, b, NOW, x)
for x in (20, 25, 30):
    LINK_STATS.add(Kind.APPROACH, "청량리", "청량리", NOW, x)


def test_eta_uses_links_when_departures_are_not_reported():
    pos = TrainPosition("r", Motion.DWELLING, "왕십리", ("청량리", "회기"), since=NOW - timedelta(seconds=155),
                        last_seen=NOW)
    r = baseline_eta(pos, "회기", NOW, LINK_STATS)
    # remaining of link 왕십리->청량리 given 155 s spent: samples {160,170} -> {5,15}, median 10; + link 140
    assert r.eta_seconds == 150
    assert [c.kind for c in r.components] == [Kind.LINK, Kind.LINK]


def test_eta_approaching_target_and_beyond():
    pos = TrainPosition("r", Motion.APPROACHING, "청량리", ("회기",), since=NOW - timedelta(seconds=10), last_seen=NOW)
    assert baseline_eta(pos, "청량리", NOW, LINK_STATS).eta_seconds == 15   # {10,15,20} -> 15
    assert baseline_eta(pos, "회기", NOW, LINK_STATS).eta_seconds == 15 + 140


def test_backtest_on_synthetic_days_is_accurate():
    stations = ["용산", "이촌", "서빙고", "한남", "옥수", "응봉", "왕십리", "청량리"]
    train_days = []
    for d in (21, 22, 23, 24):  # Mon-Thu history
        for n in range(12):
            train_days += simulate_trip(f"{d}{n:02d}", stations, kst(2026, 9, d, 8, 0) + timedelta(minutes=8 * n),
                                        segment_seconds=120, dwell_seconds=30, poll=10)
    stats = build_stats(train_days, NET, min_samples=10)
    test = []
    for n in range(4):
        test += simulate_trip(f"9{n:02d}", stations, kst(2026, 9, 25, 8, 0) + timedelta(minutes=8 * n),
                              segment_seconds=120, dwell_seconds=30, poll=10)
    evals = backtest_day(test, stats, NET)
    assert evals
    rows = {r["horizon"]: r for r in summarize(evals)}
    assert rows["0-5m"]["n"] > 0
    assert rows["0-5m"]["median_ae"] <= 15
    assert rows["5-10m"]["median_ae"] <= 20


def test_eta_jumps_over_unreported_stations():
    st = TravelStats(min_samples=3)
    for x in (400, 420, 440):
        st.add(Kind.LINK, "행신", "곡산", NOW, x)   # 능곡·대곡 never reported
    for x in (100, 110, 120):
        st.add(Kind.LINK, "곡산", "백마", NOW, x)
    pos = TrainPosition("r", Motion.RUNNING, "강매", ("행신", "능곡", "대곡", "곡산", "백마"), since=NOW, last_seen=NOW)
    r = baseline_eta(pos, "백마", NOW, st)
    assert [(c.kind, c.a, c.b) for c in r.components][1:] == [(Kind.LINK, "행신", "곡산"), (Kind.LINK, "곡산", "백마")]
    # an unreported station itself stays unpredictable (MISSING -> lower confidence)
    assert baseline_eta(pos, "대곡", NOW, st).confidence < r.confidence


def test_eta_interpolates_unreported_target_station():
    st = TravelStats(min_samples=3)
    for x in (540, 600, 660):
        st.add(Kind.LINK, "행신", "곡산", NOW, x)
    pos = TrainPosition("r", Motion.DWELLING, "행신", ("능곡", "대곡", "곡산"),
                        since=NOW - timedelta(seconds=60), last_seen=NOW)
    to_gok = baseline_eta(pos, "곡산", NOW, st)
    to_dae = baseline_eta(pos, "대곡", NOW, st)
    to_neung = baseline_eta(pos, "능곡", NOW, st)
    assert to_gok.eta_seconds == 540                  # conditional: {480,540,600} -> 540
    assert to_dae.eta_seconds == 600 * 2 / 3 - 60     # 2 of 3 steps, minus time spent
    assert to_neung.eta_seconds == 600 * 1 / 3 - 60
    assert to_dae.components[-1].interpolated and to_dae.confidence < to_gok.confidence
