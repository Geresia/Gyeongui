# DESIGN — Phase 1 (Collector · Train Tracking · Baseline ETA)

> PROJECT_SPEC v0.2 의 "First Task for Claude" 1~9 항목에 대한 답입니다.
> **VERIFIED** = 직접 확인함 / **DOCUMENTED** = 공식 문서 기준, 실제 응답으로 미검증 / **UNKNOWN** = 모름, 가정하지 않음

---

## 1. Spec 분석 요약

Spec 의 방향(수집기 먼저, baseline 고정, 원인 추정과 확정 구분, priority 하드코딩 금지)은 그대로 유지합니다. 검토하면서 고친 핵심은 네 가지입니다.

| # | 문제 | 조치 |
|---|---|---|
| 1 | `DynamicETA = BaseETA + CurrentDelay` 는 현재 위치 기반 BaseETA 와 **이중 계산** | `CurrentDelayEffect`(지연 열차의 향후 주행/정차 변화)로 교체. 추정 전에는 0 |
| 2 | 폴링 데이터는 이벤트의 **정확한 시각을 모름** | 이벤트에 `[time_lower, time_upper]` 구간 저장, 불확실성 큰 이벤트는 통계에서 제외 |
| 3 | 실시간 위치 API 에 `previous station`, 좌표, 시간표 시각이 **없음** | 이전역은 route pattern 으로 계산, 좌표는 정적 데이터, "시간표 대비 지연"은 시간표 확보 전까지 null |
| 4 | Phase 2 (KTX/ITX 실시간 위치) 데이터 존재가 **미확인** | Phase 2 는 데이터 확보 확인 후 착수. 미확보 시 축소안 사용 |

---

## 2. 외부 데이터 / API 목록과 등급

등급: **필수** (없으면 해당 Phase 불가) / **있으면 좋음** / **없어도 됨**

| # | 데이터 | 출처 | 상태 | Phase 1 | Phase 2+ |
|---|---|---|---|---|---|
| D1 | 경의중앙선 실시간 열차 위치 (`realtimePosition`) | 서울 열린데이터광장 `swopenapi.seoul.go.kr` | 엔드포인트 응답 **VERIFIED**, 필드 **DOCUMENTED** | **필수** | 필수 |
| D2 | 역별 실시간 도착정보 (`realtimeStationArrival`, 일괄 조회 버전 별도 존재) | 서울 열린데이터광장 | 명세서 **확인 (2026-09-25)**, 실제 레코드 미확인 | 있으면 좋음 (D1 교차검증, 기존 서비스 ETA 비교용, D1 이 안 될 때 대안) | 있으면 좋음 |
| D3 | 경의중앙선 역 순서 / 노선 구조 | 코레일 공식 노선도 + 열린데이터 `실시간도착_역정보(YYYYMMDD).xlsx` (API 가 쓰는 역 ID·역명, 매월 갱신) | 시드 CSV 작성. **xlsx 로 역명/ID 대조 필요** | **필수** | 필수 |
| D4 | 역 좌표 | 서울 열린데이터 역 위치정보 / 국가철도공단 | UNKNOWN (출처 미정) | 없어도 됨 | 있으면 좋음 (지도 UI) |
| D5 | 경의중앙선 공식 시간표 (열차번호별) | 코레일 / 공공데이터포털 | **UNKNOWN** — 기계 판독 가능한 형태 미확인 | 있으면 좋음 (없으면 "시간표 대비 지연" = null) | **필수** (sequence change, rotation) |
| D6 | KTX / ITX / 일반열차 **실시간 위치** | 코레일 | **UNKNOWN** — 공개 API 미확인 | 없어도 됨 | **필수** (Conflict) |
| D7 | 간선열차 시간표 | 공공데이터포털 (국토교통부 열차정보 계열) | UNKNOWN (범위/열차번호 형식 미확인) | 없어도 됨 | 있으면 좋음 (D6 없을 때 축소안) |
| D8 | 선로 배선 (복선/복복선, 대피선, 회차 가능역) | 국가철도공단 / 코레일 | UNKNOWN | 없어도 됨 | 필수 (Conflict 정확도) |
| D9 | 공식 운행장애 / 지연 사유 | 코레일 공지, 서울 교통 소통 정보 등 | UNKNOWN | 없어도 됨 | 있으면 좋음 (confirmed 원인) |
| D10 | 공휴일 달력 | 공공데이터포털 특일 정보 | 존재 알려짐, 미연동 | 있으면 좋음 (dayType 버킷) | 있으면 좋음 |
| D11 | 날씨 (강수, 기온, 낙뢰, 특보) | 기상청 API | 미연동 | 없어도 됨 | 있으면 좋음 (feature) |
| D12 | 화물열차 위치 | — | UNKNOWN (공개 가능성 낮음) | 없어도 됨 | 없어도 됨 |
| D13 | 차량 편성번호 / 운용표 | — | UNKNOWN | 없어도 됨 | 있으면 좋음 (없으면 rotation 은 추정만) |

### 2.1 D1 실시간 위치 API — 확인한 것 / 못 한 것

- **VERIFIED (2026-09-26 01:35 KST):** `http://swopenapi.seoul.go.kr/api/subway/{KEY}/json/realtimePosition/{start}/{end}/경의중앙선` 호출 시 JSON 응답. 운행 종료 시간대라 `INFO-200 해당하는 데이터가 없습니다` 반환 → **실제 열차 레코드는 아직 못 봤다.**
- **DOCUMENTED 필드:** `subwayId, subwayNm, statnId, statnNm, trainNo, lastRecptnDt, recptnDt, updnLine, statnTid, statnTnm, trainSttus, directAt, lstcarAt`
- 파서(`src/gjeta/sources/seoul_realtime.py`)는 위 필드를 **읽기만** 하고, 없거나 형식이 다르면 `None` 으로 두며 raw payload 는 항상 통째로 보존한다.
- **명세서로 확인한 것 (도착정보 API, 2026-09-25):**
  - `recptnDt` = "열차 도착정보를 생성한 시각". 명세서는 "현재시각 − recptnDt 만큼 열차가 더 진행한 것으로 보정해서 쓰라"고 안내한다. 추적 엔진은 이미 이벤트 시각에 `recptnDt` 를 쓰고, ETA confidence 의 staleness 도 `recptnDt` 기준으로 계산하므로 이 안내와 맞다.
  - 도착정보 API 의 `arvlCd` 는 0 진입, 1 도착, 2 출발, 3 전역출발, 4 전역진입, 5 전역도착, 99 운행중. 위치 API `trainSttus` 는 0~3 만 문서화돼 있다.
  - 주의: 필드 이름이 같아도 API 마다 뜻이 다르다. 도착정보의 `statnTid` 는 **다음역** ID 이고 종착역은 `bstatnId`/`bstatnNm` 이다. 위치 API 의 `statnTid`/`statnTnm` 은 종착역이다.
  - 샘플키(`sample`)는 '서울'역만, 한 번에 1~5건만 조회된다. 실제 수집에는 발급 인증키가 필요하다.
  - 서울시 밖 역은 미제공 (→ U11).
- **TODO (운행 시간대에 실행):** `python scripts/probe_api.py` 로 실제 응답을 `samples/` 에 저장 → 필드명·값 형식·`trainSttus` 전이 패턴 확인 → 파서/문서 수정.

### 2.2 UNKNOWN 목록 (추측하지 않음)

| ID | 질문 | 확인 방법 |
|---|---|---|
| ~~U1~~ | ~~일일 호출 한도~~ → **확인: 1,000회/일, 1회 1,000건.** 활용사례(갤러리) 등록 시 제약 해제 | 수집 전략은 SPEC §6 |
| U2 | `recptnDt` 갱신 주기 (의미는 도착정보 명세서에 "정보를 생성한 시각"으로 **확인됨**) | 연속 폴링 시 값 변화 간격 측정 |
| U3 | `updnLine` 0/1 ↔ 문산/용문 방향 대응 | 종착역(`statnTnm`)과 대조. **코드에서는 종착역 기준으로 방향을 정하고 updnLine 은 교차검증에만 사용** |
| U4 | `trainSttus` 가 역마다 빠짐없이 나오는지 | 1대 추적 로그에서 skipped/inferred 비율 측정 |
| U5 | 급행(`directAt`) 통과역 표현 | 급행 열차 로그 분석. **MVP 에서 급행은 수집만 하고 ETA 제외** |
| U6 | 같은 날 `trainNo` 재사용 여부 | `(serviceDate, trainNo)` 가 서로 떨어진 두 구간에서 나타나는지 검사 |
| U7 | API 역명 표기 (`신촌` vs `신촌(경의중앙선)`, `화전` 개명 여부 등) | 실제 응답 역명 목록 수집 → `station_aliases` 갱신 |
| U8 | 한 응답에 같은 열차가 중복으로 나오는지 | 응답 내 `trainNo` 중복 카운트 |
| U9 | 간선열차 실시간 위치 공개 여부 (D6) | 코레일 / 공공데이터포털 조사 |
| U10 | 경의중앙선 시간표 기계판독 가능 여부 (D5) | 공공데이터포털 조사 |
| **U11** | **서울시 밖 역 제공 여부.** 도착정보 명세서에 "서울시 이외의 역구간은 미제공"이라고 돼 있다. 위치 API 도 같은지 모름. 같다면 수색~양원 밖(문산·일산·구리·덕소·양평·용문 등)은 추적 불가 | probe 결과의 역명 분포 확인. **MVP 에서 가장 중요한 UNKNOWN** |

---

### 2.3 이용약관 제약 (서울 열린데이터광장, 2021-09-24 시행본)

| 조항 | 내용 | 설계 반영 |
|---|---|---|
| 제6조 ③④ | 비정상 사용 시 키 정지, 트래픽 많으면 활용사례 등록 강제 | 1,000회/일 한도 준수, 우회 금지, 한도 해제는 갤러리 등록으로 |
| 제7조 ① | 인증키 타인 제공·공개 금지 | 키는 환경변수, raw 로그에는 마스킹, 프론트는 우리 서버만 호출 |
| 제9조 ② | 서울시 대리로 오해될 행위 금지 | 공식 서비스처럼 보이는 명칭·로고 사용 안 함 |
| 제10조 ③ | 결과 노출 화면에 "서울특별시 공공데이터" 명시 | 모든 화면 상단 기준시각 옆에 표시 (화면 시안 반영) |
| 제12조 | 품질·중단 책임 없음 | stale 감지, "실제 운행과 다를 수 있음" 안내 |

## 3. 실제 구현 가능한 MVP 범위

**D1 + D3 만으로 가능한 것까지를 MVP 로 합니다.**

| 포함 | 제외 (이유) |
|---|---|
| 경의중앙선 본선 + 서울역 지선 일반(완행) 열차 | 문산–도라산 (운행 패턴 별도), 급행 ETA (U5) |
| 실시간 위치 수집 + raw 보존 | Conflict / 간선열차 (D6 UNKNOWN) |
| 열차 run 추적, 도착/출발 이벤트 (시간 구간 포함) | 시간표 대비 지연 (D5 UNKNOWN) → 대신 "평소 대비" 지연 |
| segment run / dwell 계산 | Rotation 확정 (D13 UNKNOWN) |
| 시간대 버킷 통계 + fallback | ML |
| **Baseline ETA** (역별 도착, 범위, confidence) | 프론트엔드 |
| 모든 예측 저장 + 실제 도착과 비교 평가 | |

"평소 대비 지연" 정의: 열차가 현재 위치까지 오는 데 걸린 실제 시간 − 같은 경로의 historical median 합. 시간표가 없어도 계산 가능하고, 사용자에게 보여주는 "평소보다 +3분" 은 이 값이다.

---

## 4. PostgreSQL Schema

전체 DDL: [`db/schema.sql`](../db/schema.sql). 계층 구조:

```
raw_api_responses          (HTTP 응답 원문, 1 poll = 1 row)       ← 절대 삭제 안 함
  └─ train_observations    (응답 속 열차 1대 = 1 row, 원본 코드 + 정규화 값)
       └─ train_station_events   (APPROACH / ARRIVAL / DEPARTURE, time_lower/upper, inferred)
            ├─ segment_runs      (DEPARTURE(i) → ARRIVAL(i+1))
            └─ station_dwells    (ARRIVAL(i) → DEPARTURE(i))
                 └─ segment_stats / dwell_stats   (버킷별 p20/p50/p80/p95, n)

stations, station_aliases, route_patterns, route_pattern_stations   (정적)
train_runs                                   (serviceDate + trainNo)
eta_predictions → eta_evaluations            (예측 저장 및 사후 평가)
collector_runs                               (수집기 가동 기록, 결측 구간 판단용)
train_conflicts, delay_events, train_rotations  (Phase 2+ 자리만 잡아둠)
```

설계 원칙:
- **재처리 가능성.** `raw_api_responses` 만 있으면 아래 모든 테이블을 다시 만들 수 있어야 한다. 이벤트/통계 테이블에는 `processor_version` 을 둔다.
- **원본 코드 보존.** `status_code`(원본 `trainSttus`) 와 정규화된 `status` 를 둘 다 저장.
- **시간은 전부 `TIMESTAMPTZ`**, 서비스 날짜는 `service_date DATE` 별도 컬럼 (KST 03:00 경계).
- 시계열이 커지면 `raw_api_responses`, `train_observations` 를 TimescaleDB hypertable 로 전환 (Phase 1 에선 일반 테이블 + 날짜 인덱스).

---

## 5. Data Collector 아키텍처

```
┌──────────── Collector (프로세스 1개) ────────────┐
│ Scheduler: 운행시간대(05:00~01:00 KST)만, 기본 15s   │
│   │                                             │
│   ▼                                             │
│ SeoulRealtimeClient.fetch_positions("경의중앙선")  │
│   │  timeout 10s, retry 3회 (지수 backoff)        │
│   │  응답 원문 + HTTP status + latency 기록         │
│   ▼                                             │
│ RawStore.append(raw)  ◄── 파싱 실패해도 먼저 저장     │
│   ▼                                             │
│ parse_position_response(raw) → [PositionObservation]│
│   ▼                                             │
│ ObservationStore.append(obs)                    │
│   ▼                                             │
│ (옵션) TrainTracker.ingest(obs) → events 실시간 생성  │
└─────────────────────────────────────────────────┘
```

- **저장 순서:** raw 먼저 → 파싱. 파서 버그가 있어도 데이터는 잃지 않는다.
- **Phase 1 저장소:** 인프라 없이 바로 돌리도록 기본은 날짜별 **JSONL** (`data/raw/YYYY-MM-DD.jsonl`). PostgreSQL 적재기(`jsonl → schema.sql`)는 M1 후반 작업 (TODO).
- **Rate limit (1,000회/일):** 재시도도 1회로 센다. 날짜별 호출 수를 `data/calls/YYYY-MM-DD.txt` 에 저장해 재시작해도 초기화되지 않는다. 수집 시간대(`--window`, 기본 운행시간 / 권장 `06:30-10:30`) 안에서 남은 호출 수가 부족하면 간격을 자동으로 늘려 시간대 끝까지 버틴다.
- **에러 코드 처리:** API 는 HTTP 200 안에 `status/code` 로 에러를 준다 (예: `INFO-200` 데이터 없음). 이 경우도 raw 에 저장하고 `api_code` 컬럼으로 구분.
- **Stale 감지:** 전체 응답에서 `recptnDt` 최대값이 N분 이상 안 바뀌면 "데이터 장애" 로 기록 (열차 정차와 구분, SPEC §26).

---

## 6. Train State Engine 아키텍처

### 6.1 Route pattern 과 방향

- `route_pattern` = 순서가 있는 역 목록. MVP: `MAIN`(문산→…→용산→청량리→…→지평), `SEOUL_BRANCH`(서울역→신촌→가좌→…→문산).
- 방향은 `updnLine` 이 아니라 **종착역 위치**로 결정: pattern 안에서 종착역 index > 현재역 index 이면 `+1`, 작으면 `-1`. `updnLine` 은 교차검증용 (U3).
- Pattern 선택: 현재역과 종착역을 둘 다 포함하는 pattern. 여러 개면(예: 가좌 이후 문산행) 이 run 에서 이미 쓰던 pattern 유지, 없으면 `MAIN`. 가좌–문산 구간은 두 pattern 이 같은 역 순서라 어느 쪽을 골라도 결과가 같다.
- 통계 키는 pattern 과 무관하게 **물리적 역 쌍**으로 둔다: segment = `(from, to)`, dwell = `(station, next_station)`. 그래서 서울역 지선 열차와 본선 열차가 공용 구간 통계를 같이 쓴다.

### 6.2 진행 서수(progress ordinal) 모델 — 이벤트 생성 알고리즘

`trainSttus` 네 상태를 하나의 단조 증가 축으로 바꿉니다. 운행 방향으로 k 번째 역에서:

| API 상태 | 의미 | milestone | ordinal |
|---|---|---|---|
| 0 진입 | k역 진입 중 | APPROACH(k) | 3k + 0 |
| 1 도착 | k역 도착 | ARRIVAL(k) | 3k + 1 |
| 2 출발 | k역 출발 | DEPARTURE(k) | 3k + 2 |
| 3 전역출발 | k-1역 출발함 | DEPARTURE(k-1) | 3(k-1) + 2 |

`전역출발(k)` 와 `출발(k-1)` 은 같은 물리 상태라서 같은 ordinal 로 합칩니다.

열차 run 별로 관측이 들어올 때:

```
new = ordinal(obs)
if run 첫 관측:            last = new, 이벤트 없음 (언제 그 상태가 됐는지 모르므로)
elif new <= last:          이벤트 없음 (정체 또는 역행 → 역행은 anomaly 카운트)
else:
    for m in (last+1 .. new):          # 지나간 milestone 전부
        event(m, time_lower = 이전 관측 시각,
                 time_upper = 이번 관측 시각,
                 inferred   = (m != new))   # 건너뛴 milestone 은 추론
    last = new
```

- 이벤트 시각 추정값 = 구간 중앙값. 불확실성 = `(upper - lower) / 2`.
- 관측 시각은 `recptnDt`(원천 시각)이 있고 정상 범위면 그것을, 아니면 `observedAt` 을 쓴다.
- **연속 관측 간격이 너무 크면**(수집기 중단 등, 기본 180초 초과) 그 사이 milestone 은 이벤트를 만들지 않고 run 을 "gap" 으로 표시한다. 가짜 긴 정차/주행이 통계를 오염시키는 걸 막는다.
- 종착역 ARRIVAL 이후 또는 N분 미관측 시 run 종료.

이 로직은 I/O 없는 순수 클래스 `TrainTracker` 이고, 실시간/재처리 모두 같은 코드를 씁니다.

### 6.3 구간/정차 파생과 통계

- `dwell(k) = DEPARTURE(k) - ARRIVAL(k)`
- `segment(k→k+1) = ARRIVAL(k+1) - DEPARTURE(k)`
- 두 이벤트 모두 inferred 가 아니고, 불확실성 합이 `MAX_UNCERTAINTY`(기본 20초) 이하일 때만 `usable=true`. 통계는 usable 만 사용.
- 통계 키와 fallback 순서:

```
L0 (역 쌍, dayType, timeBand)
L1 (역 쌍, dayType)
L2 (역 쌍)
L3 없음 → 해당 ETA 는 UNKNOWN 구간 포함으로 표시
```

  - `dayType`: `WEEKDAY` / `SATURDAY` / `SUNDAY_HOLIDAY` (공휴일은 D10 연동 전까지 평일로 분류 = TODO)
  - `timeBand`: `EARLY` 05-07, `AM_PEAK` 07-09, `MIDDAY` 09-17, `PM_PEAK` 17-20, `EVENING` 20-03
  - 각 레벨은 표본 수 `n >= MIN_SAMPLES`(기본 10) 이어야 채택.

---

## 7. Baseline ETA 알고리즘 (M2)

**입력:** 열차 현재 상태(마지막 milestone, 그 시각), 목표역, 현재시각 `now`, 통계.
**출력:** `eta_seconds`(p50), `lower`(p20), `upper`(p80), `confidence`, 사용된 fallback 레벨들.

```
elapsed = now - last_milestone_time

1) 현재 진행 중인 구간의 "남은 시간"
   - 마지막이 ARRIVAL(k)   → 정차 중:  remaining = CondRemain(dwell(k), elapsed)
   - 마지막이 DEPARTURE(k) → 주행 중:  remaining = CondRemain(segment(k→k+1), elapsed)
   - 마지막이 APPROACH(k)  → 거의 도착: remaining = CondRemain(approach(k), elapsed)  [표본 없으면 0]

2) 이후 목표역까지:
   Σ segment(j→j+1) + Σ dwell(j)  (중간역만, 목표역 dwell 제외)

3) ETA = remaining + 2)
```

**조건부 잔여시간 `CondRemain(X, e)`**: 과거 표본 중 `x > e` 인 것만 골라 `x - e` 의 분위수를 씀.
이미 오래 서 있는 열차일수록 남은 시간이 늘어나는 걸 자연스럽게 반영합니다.
`x > e` 인 표본이 하나도 없으면 → **이상 정차 신호**: remaining = 0, `abnormal=true`, confidence 크게 감소 (SPEC §26 연결 지점).

**범위(p20~p80):** 구간끼리 지연이 양의 상관관계라서 독립 가정(분산 합)은 범위를 너무 좁게 만듭니다. 초기엔 **완전 상관(comonotonic) 가정 = 분위수를 그대로 합산**합니다. 보수적으로 넓은 범위이고, `eta_evaluations` 의 coverage 로 나중에 보정합니다.

**Confidence (0~1, 휴리스틱 v0):**

```
confidence = Π over used stats ( level_weight[L] )  ×  staleness_factor  ×  (abnormal ? 0.3 : 1)
level_weight: L0=1.0, L1=0.95, L2=0.85, 누락=0.5
staleness_factor = 1 (관측 30초 이내) → 선형 감소 → 0.5 (180초)
```

이 값은 "확률"이 아니라 **순위용 점수**로만 씁니다. 실제 적중률로 calibration 하는 건 M2 평가 이후.

**평가 (M2 완료 기준):** 예측할 때마다 `eta_predictions` 에 저장 → 해당 열차의 실제 ARRIVAL 이벤트가 생기면 `eta_evaluations` 생성 → horizon 버킷(0-5, 5-10, 10-20, 20-40, 40+분)별 MAE / Median AE / P80 / P95 / bias / coverage.
- horizon 버킷은 **예측 ETA 기준**으로 나눈다. 실제 소요시간 기준으로 나누면 느린 열차가 긴 버킷에 몰려서 bias 가 생긴다.
- 실제 도착 자체도 ±(폴링 간격/2) 오차가 있음을 리포트에 같이 표시.
- Backtest 는 **테스트 날짜 이전 날짜의 데이터로만** 통계를 만든다 (데이터 누수 방지). `scripts/replay.py --backtest`.

**가상 데이터 smoke test 결과 (실제 성능 아님):** 15초 폴링, 11개 역, 5일 × 15편성 가상 데이터에서 0-5분 median error 15초, 10-20분 60초.
긴 horizon 에서 음(-)의 bias(과소예측)가 보였는데, "이번 구간이 느린 열차는 다음 구간도 느리다"는 상관관계를 baseline 이 모르기 때문이다. M3 의 Current delay effect 가 바로 이걸 다룬다.

---

## 8. 다음 단계 (M3 이후 설계 메모)

- **Current delay effect (M3):** "평소 대비 지연" 구간별로 segment/dwell 잔차 분포를 따로 집계 → 차이가 유의미할 때만 적용.
- **Preceding train (M3):** 같은 pattern/방향에서 바로 앞 열차의 ordinal 차이 → 시간 gap. gap 이 좁을 때 뒤 열차 segment 잔차 분포를 학습 (threshold 하드코딩 안 함).
- **Conflict (M4):** D6 확보 전 착수 안 함.

---

## 9. 코드 구조 (Phase 1)

```
src/gjeta/
  timeutil.py              KST, service date, dayType/timeBand
  stations.py              route pattern, 역명 정규화, 방향 계산
  data/gyeongui_jungang_stations.csv   시드 (API 역명으로 검증 필요)
  models.py                PositionObservation, StationEvent, SegmentRun, StationDwell
  sources/seoul_realtime.py    API client + 방어적 파서
  tracking/tracker.py      progress ordinal 기반 이벤트 생성
  tracking/derive.py       segment run / dwell 파생
  eta/stats.py             분위수, 버킷, fallback
  eta/baseline.py          Baseline ETA
  collector.py             폴링 루프 + JSONL 저장
scripts/probe_api.py       실제 응답 샘플 저장
scripts/replay.py          JSONL → 이벤트/구간/정차 재처리 요약
db/schema.sql
tests/
```
