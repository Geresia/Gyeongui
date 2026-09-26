"""Korail run records: 4 real trains from run_ymd=20260925 (공공데이터포털 한국철도공사_열차운행정보)."""

import json
from pathlib import Path

from gjeta.sources.korail_run import infer_train_type, parse_run_info_items
from gjeta.timeutil import KST

FIXTURE = Path(__file__).parent / "fixtures" / "korail_run_info_20260925_sample.json"


def test_parse_real_rows():
    stops = parse_run_info_items(json.loads(FIXTURE.read_text(encoding="utf-8")))
    by_train = {}
    for s in stops:
        by_train.setdefault(s.train_no, []).append(s)
    assert set(by_train) == {"00801", "02053", "01662", "00001"}
    first = min(by_train["00001"], key=lambda s: s.seq)
    assert first.station_name == "서울" and first.stop_kind == "시발" and first.arrival is None
    assert first.departure.tzinfo == KST and first.departure.hour == 5
    assert any(s.station_name == "덕소" for s in by_train["01662"])


def test_infer_train_type_is_conservative():
    assert infer_train_type("02053", "경춘선") == "ITX_CHEONGCHUN"
    assert infer_train_type("00801", "강릉선") == "KTX_EUM"
    assert infer_train_type("00001", "경부선") == "UNKNOWN"
