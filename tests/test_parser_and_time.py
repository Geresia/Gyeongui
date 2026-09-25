from datetime import date, datetime

from gjeta.models import ObsStatus
from gjeta.sources.seoul_realtime import RawResponse, parse_position_record, parse_position_response
from gjeta.stations import load_default_network, normalize_name
from gjeta.timeutil import KST, DayType, TimeBand, day_type, parse_seoul_timestamp, service_date, time_band

from helpers import kst, record


def test_parse_timestamp_formats():
    assert parse_seoul_timestamp("2026-09-26 08:31:05") == kst(2026, 9, 26, 8, 31, 5)
    assert parse_seoul_timestamp("2026-09-26 08:31:05.0") == kst(2026, 9, 26, 8, 31, 5)
    assert parse_seoul_timestamp("") is None
    assert parse_seoul_timestamp("garbage") is None


def test_service_date_cutoff_at_3am():
    assert service_date(kst(2026, 9, 26, 0, 40)) == date(2026, 9, 25)
    assert service_date(kst(2026, 9, 26, 5, 10)) == date(2026, 9, 26)


def test_buckets():
    assert time_band(kst(2026, 9, 25, 8, 0)) is TimeBand.AM_PEAK
    assert time_band(kst(2026, 9, 26, 0, 30)) is TimeBand.EVENING
    assert day_type(kst(2026, 9, 26, 12, 0)) is DayType.SATURDAY  # 2026-09-26 is a Saturday
    assert day_type(kst(2026, 9, 27, 0, 30)) is DayType.SATURDAY  # after midnight = Saturday service


def test_parse_record_keeps_raw_and_codes():
    t = kst(2026, 9, 25, 8, 31, 5)
    rec = record("5123", "왕십리", "DEPART", "용문", t)
    o = parse_position_record(rec, observed_at=t)
    assert o.status is ObsStatus.DEPARTED
    assert o.status_code == "2"
    assert o.train_run_id == "2026-09-25:5123"
    assert o.raw is rec
    assert o.is_express is False


def test_unknown_status_and_future_source_time():
    t = kst(2026, 9, 25, 8, 31, 5)
    rec = record("5123", "왕십리", "DEPART", "용문", kst(2026, 9, 25, 9, 0))
    rec["trainSttus"] = "9"
    o = parse_position_record(rec, observed_at=t)
    assert o.status is ObsStatus.UNKNOWN
    assert o.source_generated_at is None  # 29 min in the future -> not trusted


def test_parse_response_empty_and_error_bodies():
    base = dict(source="s", request_url="u", requested_at=kst(2026, 9, 25, 1, 0),
                observed_at=kst(2026, 9, 25, 1, 0), latency_ms=1, http_status=200, body_text=None, error=None)
    # Real response observed 2026-09-26 01:35 KST outside service hours.
    empty = RawResponse(body={"status": 500, "code": "INFO-200", "message": "해당하는 데이터가 없습니다.",
                              "link": "", "developerMessage": "", "total": 0}, **base)
    assert empty.api_code == "INFO-200"
    assert parse_position_response(empty) == []
    ok = RawResponse(body={"errorMessage": {"code": "INFO-000"},
                           "realtimePositionList": [record("1", "상봉", "ARRIVE", "용문", kst(2026, 9, 25, 1, 0))]},
                     **base)
    assert ok.api_code == "INFO-000"
    assert len(parse_position_response(ok)) == 1


def test_station_names():
    net = load_default_network()
    assert normalize_name("신촌(경의중앙선)") == "신촌"
    assert net.resolve("왕십리역") == "왕십리"
    assert net.resolve("서울") == "서울역"
    assert net.resolve("DMC") == "디지털미디어시티"
    assert net.resolve("강남") is None  # unknown names are never guessed


def test_pattern_choice_prefers_current():
    net = load_default_network()
    assert net.choose_pattern("대곡", "문산").pattern_id == "MAIN"
    assert net.choose_pattern("대곡", "문산", "SEOUL_BRANCH").pattern_id == "SEOUL_BRANCH"
    assert net.choose_pattern("신촌", "문산").pattern_id == "SEOUL_BRANCH"
    assert net.choose_pattern("신촌", "용문") is None
