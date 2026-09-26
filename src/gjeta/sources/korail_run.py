"""Korail passenger train run records (공공데이터포털 한국철도공사_열차운행정보).

Verified 2026-09-26 against run_ymd=20260925:
- `travelerTrainRunInfo2`: per train and stop, arrival/departure times. They are the
  *actual* times (train 00001: plan arrival 07:50, record 07:52), minute resolution,
  available from the next day (retention: 3 months back to 1 day ago).
- `travelerTrainRunPlan2`: per train, planned origin/terminal departure/arrival.
- Only intercity passenger trains (965 on a weekday), NOT metropolitan electric trains:
  경의중앙선 itself is absent, so this cannot fill 능곡·대곡 or the 서울역 branch.
- Only stops are listed; a train passing a station without stopping has no row there.
- There is no train-type field. Train type is inferred from the number (see
  `infer_train_type`) and must be treated as a guess.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..timeutil import KST

BASE_URL = "https://apis.data.go.kr/B551457/run/v2"
PAGE_SIZE = 1000


@dataclass(frozen=True)
class TrunkStop:
    run_date: date
    train_no: str          # 5 digits, zero padded as in the API
    line_name: str         # mrnt_nm, e.g. 강릉선, 중앙선, 경춘선
    station_name: str
    station_code: str
    seq: int               # trn_run_sn
    stop_kind: str         # 시발 / 여객승하차 / 종착
    direction: str | None  # uppln_dn_se_cd (U/D)
    arrival: datetime | None
    departure: datetime | None

    @property
    def time(self) -> datetime | None:
        """Best single time at this stop (departure, else arrival)."""
        return self.departure or self.arrival


def _ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).split(".", 1)[0]
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return None


def parse_run_info_items(items: list[dict[str, Any]]) -> list[TrunkStop]:
    out = []
    for r in items:
        try:
            run_date = datetime.strptime(str(r["run_ymd"]), "%Y%m%d").date()
            seq = int(r["trn_run_sn"])
        except (KeyError, ValueError):
            continue
        out.append(
            TrunkStop(
                run_date=run_date,
                train_no=str(r.get("trn_no", "")),
                line_name=str(r.get("mrnt_nm") or ""),
                station_name=str(r.get("stn_nm") or ""),
                station_code=str(r.get("stn_cd") or ""),
                seq=seq,
                stop_kind=str(r.get("stop_se_nm") or ""),
                direction=r.get("uppln_dn_se_cd"),
                arrival=_ts(r.get("trn_arvl_dt")),
                departure=_ts(r.get("trn_dptre_dt")),
            )
        )
    return out


def infer_train_type(train_no: str, line_name: str) -> str:
    """Rough train type from the number and line. A guess, never shown as fact.

    Grounded only in what the 2026-09-25 data shows: 경춘선 020xx matches ITX-청춘
    (2053 was also flagged directAt=9 in the Seoul feed); 강릉선 008xx and 중앙선 007xx
    run 청량리-강릉/부전 (KTX-이음 services); 016xx on 중앙선/태백선 are conventional
    trains. Anything else is UNKNOWN.
    """
    n = int(train_no) if train_no.isdigit() else -1
    if line_name == "경춘선" and 2000 <= n < 2100:
        return "ITX_CHEONGCHUN"
    if line_name == "강릉선" and 800 <= n < 900:
        return "KTX_EUM"
    if line_name == "중앙선" and 700 <= n < 800:
        return "KTX_EUM"
    if line_name in ("중앙선", "태백선") and 1600 <= n < 1700:
        return "CONVENTIONAL"
    return "UNKNOWN"


def _get(op: str, key: str, params: dict[str, str], timeout: float = 90.0, retries: int = 4) -> dict[str, Any]:
    query = {"serviceKey": key, "returnType": "json", **params}
    url = f"{BASE_URL}/{op}?{urllib.parse.urlencode(query)}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{op} failed after {retries} attempts: {last}")


def fetch_all(op: str, key: str, run_date: date) -> list[dict[str, Any]]:
    """All rows of one operation for one run date (paged)."""
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        body = _get(op, key, {"pageNo": str(page), "numOfRows": str(PAGE_SIZE),
                              "cond[run_ymd::EQ]": run_date.strftime("%Y%m%d")})["response"]["body"]
        got = body.get("items")
        batch = got.get("item", []) if isinstance(got, dict) else []
        items.extend(batch)
        if not batch or len(items) >= int(body.get("totalCount", 0)):
            return items
        page += 1
        time.sleep(1)
