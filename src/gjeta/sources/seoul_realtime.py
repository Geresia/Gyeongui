"""Seoul Open Data real-time subway API (swopenapi.seoul.go.kr).

Endpoint reachability is verified; field names follow the official documentation
and are NOT yet verified against a live train record (DESIGN §2.1). The parser only
reads documented fields, never invents values, and always keeps the raw record.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import ObsStatus, PositionObservation
from ..timeutil import KST, parse_seoul_timestamp, service_date

BASE_URL = "http://swopenapi.seoul.go.kr/api/subway"
LINE_NAME = "경의중앙선"
MAX_ROWS = 1000  # documented per-request maximum

STATUS_CODES: dict[str, ObsStatus] = {
    "0": ObsStatus.APPROACHING,
    "1": ObsStatus.ARRIVED,
    "2": ObsStatus.DEPARTED,
    "3": ObsStatus.PREV_DEPARTED,
}


@dataclass(frozen=True)
class RawResponse:
    source: str
    request_url: str        # API key masked
    requested_at: datetime
    observed_at: datetime
    latency_ms: int
    http_status: int | None
    body: Any | None        # parsed JSON, or None
    body_text: str | None   # only when body is not JSON
    error: str | None

    @property
    def api_code(self) -> str | None:
        return _api_code(self.body)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "request_url": self.request_url,
            "requested_at": self.requested_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "api_code": self.api_code,
            "body": self.body,
            "body_text": self.body_text,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RawResponse":
        return cls(
            source=d["source"],
            request_url=d["request_url"],
            requested_at=datetime.fromisoformat(d["requested_at"]),
            observed_at=datetime.fromisoformat(d["observed_at"]),
            latency_ms=d.get("latency_ms", 0),
            http_status=d.get("http_status"),
            body=d.get("body"),
            body_text=d.get("body_text"),
            error=d.get("error"),
        )


def _api_code(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    # Error/empty responses put code at top level; success responses nest it.
    if "code" in body:
        return str(body["code"])
    msg = body.get("errorMessage")
    if isinstance(msg, dict) and "code" in msg:
        return str(msg["code"])
    return None


def position_url(api_key: str, line: str = LINE_NAME, start: int = 0, end: int = MAX_ROWS - 1) -> str:
    return f"{BASE_URL}/{urllib.parse.quote(api_key)}/json/realtimePosition/{start}/{end}/{urllib.parse.quote(line)}"


def _mask(url: str, api_key: str) -> str:
    return url.replace(urllib.parse.quote(api_key), "***") if api_key else url


def fetch_positions(api_key: str, line: str = LINE_NAME, timeout: float = 10.0) -> RawResponse:
    """One HTTP call. Never raises for network/HTTP errors; they are recorded in the result."""
    url = position_url(api_key, line)
    requested_at = datetime.now(KST)
    t0 = time.monotonic()
    status: int | None = None
    text: str | None = None
    error: str | None = None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status = resp.status
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        status = e.code
        text = e.read().decode("utf-8", errors="replace")
        error = f"HTTPError {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        error = f"{type(e).__name__}: {e}"
    latency_ms = int((time.monotonic() - t0) * 1000)
    body: Any | None = None
    body_text: str | None = None
    if text is not None:
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body_text = text
    return RawResponse(
        source="seoul_realtime_position",
        request_url=_mask(url, api_key),
        requested_at=requested_at,
        observed_at=datetime.now(KST),
        latency_ms=latency_ms,
        http_status=status,
        body=body,
        body_text=body_text,
        error=error,
    )


def _str(record: dict[str, Any], key: str) -> str | None:
    v = record.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def parse_position_record(record: dict[str, Any], observed_at: datetime) -> PositionObservation | None:
    """Map one documented record to an observation. Returns None without a train number."""
    train_no = _str(record, "trainNo")
    if train_no is None:
        return None
    source_at = parse_seoul_timestamp(_str(record, "recptnDt"))
    # Sanity check: ignore a source time from the future or far in the past;
    # the raw value is still kept in `raw`.
    if source_at is not None:
        skew = (observed_at - source_at).total_seconds()
        if skew < -60 or skew > 3600:
            source_at = None
    status_code = _str(record, "trainSttus")
    last = _str(record, "lstcarAt")
    return PositionObservation(
        observed_at=observed_at,
        source_generated_at=source_at,
        service_date=service_date(source_at or observed_at),
        train_no=train_no,
        station_name=_str(record, "statnNm"),
        terminal_name=_str(record, "statnTnm"),
        api_station_id=_str(record, "statnId"),
        updn_line=_str(record, "updnLine"),
        status_code=status_code,
        status=STATUS_CODES.get(status_code or "", ObsStatus.UNKNOWN),
        express_code=_str(record, "directAt"),
        is_last_train=None if last is None else last == "1",
        raw=record,
    )


def parse_position_response(raw: RawResponse) -> list[PositionObservation]:
    body = raw.body
    if not isinstance(body, dict):
        return []
    records = body.get("realtimePositionList")
    if not isinstance(records, list):
        return []
    out = []
    for rec in records:
        if isinstance(rec, dict):
            obs = parse_position_record(rec, raw.observed_at)
            if obs is not None:
                out.append(obs)
    return out
