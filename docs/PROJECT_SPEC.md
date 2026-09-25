# Gyeongui-Jungang Real-Time ETA Predictor — PROJECT SPEC

> 버전: v0.2 (2026-09-25)
> v0.1 원안에 검토 의견을 반영한 버전입니다. 원안에서 바뀐 부분은 `> **[v0.2]**` 로 표시했습니다.
> 설계 상세(데이터 소스 등급, DB schema, 알고리즘)는 [`DESIGN.md`](./DESIGN.md) 를 봐 주세요.

---

## 0. Project Overview

경의중앙선의 실시간 열차 위치와 주변 철도 운행 데이터를 수집해서 다음 두 가지를 예측하는 시스템을 만든다.

1. 특정 경의중앙선 열차가 특정 역에 **몇 분 후 도착**하는지
2. 지금 출발하면 **A역 → B역이 실제로 얼마나 걸리는지**

지도 서비스의 시간표 기반 예상시간이나 `3정거장 전`, `전역 출발` 수준의 정보를 넘어서 아래 요소를 반영한 **Dynamic ETA** 를 계산한다.

- 현재 열차 위치
- 실제 구간별 소요시간 / 역 정차시간
- 현재 지연, 선행 열차
- KTX / ITX / 일반열차와의 운행 간섭, 신호 대기, 대피
- 지연 전파, 비정상 장시간 정차

## 1. Primary Goal

```
경의중앙선 용문행

현재 위치:       왕십리 → 청량리
청량리 예상 도착: 약 4분 후
상봉 예상 도착:   약 13분 후 (예측 범위 11~15분)
현재 예상 지연:   +2분
운행 상태:       선행 열차 영향 가능성
```

A → B 검색:

```
홍대입구 → 구리

시간표 기준:   38분
현재 실제 예상: 44분
예상 도착:     08:47
현재 추가 지연: +6분
```

## 2. Important Principle

```
Static Timetable
+ Real-Time Train Position
+ Historical Segment Travel Time
+ Current Delay
+ Other Train Movements
+ Conflict Detection
+ Delay Propagation
= Dynamic ETA
```

## 3. MVP Scope (단계별)

| Phase | 내용 | 전제 조건 |
|---|---|---|
| 1 | 경의중앙선 자체 데이터만: 위치 수집, 열차번호 추적, 도착/출발 이벤트, 구간 소요시간, 정차시간, **Baseline ETA** | 서울시 실시간 위치 API |
| 2 | 간섭 가능 열차(KTX, KTX-산천, KTX-이음, ITX-청춘, ITX-새마을, 새마을, 무궁화, 누리로, 급행 전동열차) 추가 → 대피/선행/간섭 보정 | **간선열차 실시간 위치 데이터 확보 (UNKNOWN)** |
| 3 | 지연 전파 모델 (A 지연 → B 간격 감소 → B 신호대기 → C 연쇄) | Phase 1 데이터 수 주 이상 |
| 4 | 통계/ML (LightGBM / XGBoost / CatBoost). 충분한 데이터 전 Deep Learning 금지 | baseline 대비 개선 검증 |

> **[v0.2]** Phase 2 는 "간선열차 실시간 위치를 얻을 수 있다" 는 게 확인되기 전에는 착수하지 않는다.
> 현재 공개 API 중 KTX/ITX 의 **실시간 위치**를 주는 곳은 확인되지 않았다 (DESIGN.md §2 참고).
> 확보가 안 되면 Phase 2 는 "시간표 기반 간선열차 통과 예정 + 경의중앙선 관측 정차 패턴" 으로 축소한다.

## 4. Data Sources

### 4.1 Seoul Subway Real-Time API (서울 열린데이터광장)

- 실시간 열차 위치: `realtimePosition` (노선명 `경의중앙선` 으로 조회)
- 실시간 역 도착: `realtimeStationArrival`

문서상 제공 필드 (**실제 응답으로 검증 필요 — DESIGN.md §2.1**):

| 필드 | 의미 (문서 기준) |
|---|---|
| `subwayId` | 노선 ID (경의중앙선 = `1063`) |
| `statnId`, `statnNm` | 현재 역 ID / 역명 |
| `trainNo` | 열차번호 |
| `lastRecptnDt`, `recptnDt` | 최종 수신 날짜 / 수신 시각 |
| `updnLine` | 상하행 구분 (0: 상행/내선, 1: 하행/외선) |
| `statnTid`, `statnTnm` | 종착역 ID / 종착역명 |
| `trainSttus` | 0: 진입, 1: 도착, 2: 출발, 3: 전역출발 |
| `directAt` | 급행 여부 (1: 급행, 0: 일반, 7: 특급) |
| `lstcarAt` | 막차 여부 |

> **[v0.2]** 원안의 `previous station`, `data generation timestamp` 같은 필드는 이 API 에 **별도 필드로 없다**.
> - 이전역은 노선 역 순서 + 방향으로 **계산**한다.
> - `recptnDt` 를 데이터 생성시각(`sourceGeneratedAt`) 후보로 쓰되, 실제 의미는 수집 데이터로 검증한다.
> - 위경도는 제공되지 않는다. 역 좌표는 별도 정적 데이터로 관리한다.

API 수신시각과 원천 데이터 시각은 반드시 분리 저장한다.

```json
{
  "observedAt": "2026-09-26T08:31:12+09:00",
  "sourceGeneratedAt": "2026-09-26T08:31:05+09:00"
}
```

## 5. Data Collection Rule (매우 중요)

앱 UI 보다 **Data Collector 를 먼저** 구현한다. 데이터가 쌓이지 않으면 지연 패턴도, 대피 패턴도, ETA 정확도도 계산할 수 없다.

```
PROJECT_SPEC → Data Collector → Database → Train Tracking → Segment Statistics
→ Basic ETA → Conflict Detection → A→B ETA → Frontend → Machine Learning
```

## 6. Polling

- 초기 권장 10~30초, API 제한에 따라 조정.
- raw observation 은 가능한 한 삭제하지 않는다.

> **[v0.2]** 호출량 계산: 10초 = 8,640회/일, 15초 = 5,760회/일, 30초 = 2,880회/일 (노선 1개 기준).
> 실시간 지하철 인증키의 일일 호출 한도가 확인되기 전까지 기본값은 **15초**, 운행 시간대(05:00~01:00 KST)에만 폴링한다.
> 이벤트 시각 오차는 폴링 간격에 비례하므로(§40), 폴링 간격 자체를 ETA 품질 지표로 같이 기록한다.

## 7. Core Entities

### 7.1 Train

```ts
interface Train {
  trainId: string;          // [v0.2] = serviceDate + trainNo (열차번호는 날마다 재사용되므로)
  trainNo: string;
  trainType:
    | "GYEONGUI_JUNGANG" | "KTX" | "KTX_SANCHEON" | "KTX_EUM"
    | "ITX_CHEONGCHUN" | "ITX_SAEMAEUL" | "SAEMAEUL" | "MUGUNGHWA"
    | "NURIRO" | "EXPRESS_EMU" | "FREIGHT" | "UNKNOWN";
  direction: string;
  origin?: string;
  destination?: string;
  currentStation?: string;
  previousStation?: string;
  status: "APPROACHING" | "ARRIVED" | "DEPARTED" | "BETWEEN_STATIONS" | "STOPPED" | "TERMINATED" | "UNKNOWN";
  scheduledArrival?: Date;
  scheduledDeparture?: Date;
  predictedArrival?: Date;
  delaySeconds?: number;
}
```

> **[v0.2] Train ID 규칙.** 같은 `trainNo` 가 다른 날 다시 쓰이므로 `trainRunId = {serviceDate}:{trainNo}` 로 식별한다.
> `serviceDate` 는 KST 기준 03:00 을 날짜 경계로 한다 (자정 넘어 운행하는 막차가 전날 운행으로 묶이도록).
> 같은 날 같은 번호가 재사용되는지는 UNKNOWN — 수집 데이터로 검증한다.

## 8. Train Observation

```ts
interface TrainObservation {
  id: string;
  trainId: string;
  observedAt: Date;
  sourceGeneratedAt?: Date;
  stationId?: string;
  status: string;
  latitude?: number;   // 현 API 미제공
  longitude?: number;  // 현 API 미제공
  scheduledTime?: Date;
  rawPayload?: unknown;
}
```

## 9. Station

```ts
interface Station {
  stationId: string;
  name: string;
  sequence: number;
  latitude?: number;
  longitude?: number;
  lines: string[];
  isPassingStation?: boolean;
  canOvertake?: boolean;   // UNKNOWN 인 동안은 null 로 둔다
  canTurnback?: boolean;
}
```

> **[v0.2]** 경의중앙선은 한 줄이 아니다. 최소한 아래 운행 패턴을 구분한다.
> - 본선: 문산 … 가좌 … 용산 … 청량리 … 용문 … 지평
> - 서울역 지선: 서울역 – 신촌 – 가좌 (본선 합류)
> - 문산 – 임진강 – 도라산 구간은 MVP 제외
>
> 따라서 `sequence` 는 전역 번호가 아니라 **route pattern 안에서의 순서**다.

## 10. Segment

```ts
interface Segment {
  segmentId: string;
  fromStationId: string;
  toStationId: string;
  direction: string;
  scheduledTravelSeconds?: number;
  averageTravelSeconds?: number;
  medianTravelSeconds?: number;
  p50?: number; p80?: number; p95?: number;
}
```

## 11. Station Dwell Time

```ts
interface StationDwellStats {
  stationId: string;
  direction: string;
  timeBucket: string;
  averageSeconds: number;
  medianSeconds: number;
  p80: number;
  p95: number;
}
```

## 12. Historical Time Buckets

전체 평균 하나로 계산하지 않는다. 다음 조건으로 나눌 수 있게 설계한다:
weekday/weekend, hour, rush hour, direction, train type, express/local, station, segment, holiday.

> **[v0.2]** 버킷을 너무 잘게 쪼개면 표본이 부족해진다. 그래서 **fallback 계층**을 둔다 (DESIGN.md §6.3):
> `(segment, direction, dayType, timeBand, service)` → `(segment, direction, dayType)` → `(segment, direction)` → 시간표 값 → 없음.
> 어느 단계에서 값을 가져왔는지를 confidence 에 반영한다.

## 13. Base ETA

```
BaseETA = Σ SegmentTravelTime(남은 구간) + Σ StationDwellTime(남은 중간역)
```

> **[v0.2]** 현재 구간을 이미 달리고 있는 중이면 "남은" 시간은 `median - 경과시간` 이 아니라
> **조건부 잔여시간** `median(X - e | X > e)` 로 계산한다. 이미 오래 서 있는 열차일수록 남은 시간이 길어지는 현상을 반영하기 위해서다.

## 14. Dynamic ETA

```
DynamicETA = BaseETA
  + CurrentDelayEffect
  + PrecedingTrainAdjustment
  + SignalWaitAdjustment
  + TrainConflictAdjustment
  + AbnormalStopAdjustment
  + RotationDelayAdjustment
```

> **[v0.2] 이중 계산 주의 (중요).** BaseETA 가 **현재 위치에서** 계산된다면, 지금까지 쌓인 지연은 이미 위치에 반영돼 있다.
> 여기에 `CurrentDelay`(시간표 대비 지연)를 **다시 더하면 이중 계산**이다.
> 원안의 `+ CurrentDelay` 는 `+ CurrentDelayEffect` 로 바꾼다 = "지금 늦은 열차는 **앞으로** 구간/정차시간이 평소와 얼마나 다른가" (예: 늦은 열차는 승객이 몰려 정차가 길어짐, 또는 회복운전으로 짧아짐).
> 이 값은 추측으로 넣지 않고 관측 데이터로 추정하며, 추정 전에는 0 이다.
> 같은 이유로 §31, §34 예시 계산도 수정했다.

## 15. Delay Cause Model

```ts
enum DelayCause {
  TRAIN_CONFLICT, EXPRESS_OVERTAKE, SIGNAL_WAIT, CONGESTION,
  PRECEDING_TRAIN_DELAY, TRAIN_SEQUENCE_CHANGE, ROLLING_STOCK_ROTATION,
  ABNORMAL_STOP,
  ROLLING_STOCK_FAILURE, DOOR_FAILURE,
  TRACK_FAILURE, POWER_FAILURE, SIGNAL_FAILURE, CATENARY_FAILURE,
  LEVEL_CROSSING_INCIDENT, VEHICLE_INTRUSION, TRESPASS, FATALITY,
  PASSENGER_INCIDENT, EMERGENCY_RESPONSE,
  HEAVY_RAIN, LIGHTNING, EXTREME_HEAT, FOREIGN_OBJECT,
  SINGLE_TRACK_OPERATION, SHORT_TURN, SERVICE_SUSPENSION,
  UNKNOWN
}
```

## 16. Priority Train Model

아래 숫자는 코레일 관제의 공식 우선순위가 **아니다**. 초기 Conflict Prediction 에서 쓰는 working priority score 이고, 관측 결과로 계속 보정한다.

```ts
enum TrainPriority {
  KTX = 100, KTX_SANCHEON = 100,
  KTX_EUM = 90,
  ITX_CHEONGCHUN = 80,
  ITX_SAEMAEUL = 70, SAEMAEUL = 70, MUGUNGHWA = 70, NURIRO = 70,
  EXPRESS_EMU = 65,
  GYEONGUI_JUNGANG_LOCAL = 50,
  EXPRESS_FREIGHT = 40, FREIGHT = 30
}
```

절대 이렇게 구현하지 않는다:

```ts
if (ktxNearby) delay += 180;
```

실제 지연은 위치와 선로 충돌 여부로 계산한다.

## 17. Train Conflict Detection

두 열차가 있다고 해서 반드시 충돌하는 건 아니다. 확인할 것:

- 같은 선로인가? 같은 방향인가? 반대 방향인가?
- 같은 segment 를 쓸 예정인가? 진입 시간이 겹치는가?
- 추월 가능한 역이 있는가?
- 선행 열차인가? 상위 열차가 후방에서 접근하는가?

> **[v0.2]** "같은 선로" 판단에는 복선/복복선 구간, 선로 공용 구간 정보가 필요하다. 공개 자료로 확보 가능한지 UNKNOWN (§66-4).

## 18. Conflict Object

```ts
interface TrainConflict {
  targetTrainId: string;
  relatedTrainId: string;
  targetPriority: number;
  relatedPriority: number;
  segmentId: string;
  expectedTargetEntry: Date;
  expectedRelatedEntry: Date;
  timeDifferenceSeconds: number;
  conflictProbability: number;
  expectedDelaySeconds: number;
  confidence: number;
}
```

## 19. Conflict Example

```
경의중앙선 K5123
현재: 덕소
3분 뒤: KTX-이음이 동일 병목구간 접근 예정
과거 동일 패턴: 경의중앙선 평균 대기 210초
Conflict Probability: 0.81
ETA Adjustment: +170 sec   (= 0.81 × 210)
```

## 20. IMPORTANT: Causality Rule

공식 지연 사유가 없으면 "KTX 때문에 지연됨" 이라고 단정하지 않는다. → "간선열차 운행 영향 가능성" 으로 표시한다.

```json
{ "cause": "TRAIN_CONFLICT", "confirmed": false, "confidence": 0.82 }
```

## 21. Preceding Train Model

```ts
interface PrecedingTrainState {
  targetTrainId: string;
  precedingTrainId?: string;
  stationGap?: number;
  estimatedTimeGapSeconds?: number;
  precedingTrainDelaySeconds?: number;
  gapTrend: "INCREASING" | "DECREASING" | "STABLE" | "UNKNOWN";
}
```

> **[v0.2]** 선행 열차 = 같은 route pattern, 같은 방향에서 **바로 앞에서 같은 선로를 쓰는 열차**.
> 서울역 지선 열차와 본선 열차는 가좌 합류 이후부터 선행 관계가 생긴다.

## 22. Headway Analysis

간격이 일정 이하로 좁아지면 뒤 열차의 신호 대기 가능성이 올라간다. 단, threshold 는 임의로 정하지 않고 관측 데이터에서 학습한다.

## 23. Delay Propagation

```
Incident → Train A +8분 → Train B gap 감소 → Train B +3분 → Train C +1분
```

최소 `target`, `preceding 1`, `preceding 2`, `following 1` 을 같이 추적한다.

## 24. Train Sequence Change

계획 순서 `A B C` → 실제 `B A C` 면 `sequenceChanged = true` 를 기록한다. 강한 disruption signal 이다.

> **[v0.2]** "계획 순서" 를 알려면 시간표가 필요하다. 시간표 확보 전에는 **관측 순서 변화**(같은 구간에서 앞뒤가 바뀜)만 기록한다.

## 25. Rolling Stock Rotation

```
K5100 → 문산 도착 → 회차 / 차량기지 → K5147
```

K5100 이 10분 늦으면 K5147 도 출발 전부터 늦을 수 있다.

```ts
interface TrainRotation {
  inboundTrainId: string;
  outboundTrainId: string;
  scheduledTurnaroundSeconds: number;
  predictedInboundDelaySeconds: number;
  predictedOutboundDelaySeconds: number;
}
```

> **[v0.2]** 공개 데이터에 차량 편성번호가 없으면 연결관계는 "종착 도착 → 같은 역에서 N분 내 반대방향 출발" 패턴으로 **추정**만 가능하다. 추정 결과에는 `confirmed=false` 를 붙인다.

## 26. Abnormal Stop Detection

```
해당 역 정상 정차시간 P95: 95초
현재 정차: 260초
→ { "cause": "ABNORMAL_STOP", "anomalyScore": 0.93 }
```

> **[v0.2]** "정차 중" 판단은 도착 이벤트 이후 출발 이벤트가 없는 상태가 이어지는 것으로 한다.
> 단, **API 데이터가 멈춘 것(stale)** 과 **열차가 멈춘 것**을 구분해야 한다 — 같은 시점에 다른 열차들은 위치가 갱신되는데 이 열차만 안 바뀌면 열차 정차, 전 노선이 안 바뀌면 데이터 장애로 본다.

## 27. Incident Detection

차량 고장, 출입문 고장, 선로장애, 신호장애, 전차선 장애, 정전, 사고, 선로침입, 건널목 사고, 사상사고, 폭우, 폭염, 낙뢰, 탈선.
Incident 가 발생하면 정상 ETA 모델 신뢰도를 낮춘다.

## 28. Confidence Interval

잘못된 예: `6분 13초 후 도착` / 좋은 예: `약 6분 후, 예상 범위 5~8분`

## 29. ETA Response

```ts
interface ETAResponse {
  trainId: string;
  stationId: string;
  predictedArrival: Date;
  etaSeconds: number;
  lowerBoundSeconds: number;
  upperBoundSeconds: number;
  currentDelaySeconds: number;  // 시간표 없으면 null
  confidence: number;
  factors: ETAFactor[];
  modelVersion: string;         // [v0.2] baseline vs 새 모델 비교용
}
```

## 30. ETA Factor

```ts
interface ETAFactor {
  type: DelayCause;
  adjustmentSeconds: number;
  confidence: number;
  confirmed: boolean;
  relatedTrainId?: string;
}
```

## 31. Example ETA Calculation

> **[v0.2]** 원안은 `Current delay +70` 을 Base ETA 에 더했는데, §14 이유로 이중 계산이다. 수정:

```
Base ETA (현재 위치 기준, 역사적 median)   480 sec
Current delay effect (지연열차 정차 증가)   +20
Preceding train                        +35
KTX conflict (확률 × 기대 대기)           +95
Abnormal dwell                         +20
---------------------------------------------
Final ETA                              650 sec
```

사용자 출력:

```
약 11분 후
평소보다 약 3분 지연   ← "평소" = 같은 위치에서 historical median 대비
예상 범위: 9~13분
```

## 32. A → B Dynamic Travel Time

```json
{ "from": "홍대입구", "to": "구리", "departureTime": "NOW" }
```

```
현재시각 → A역 접근 열차 후보 → 각 후보 ETA(A) → A 탑승 가능시간 → A→B Dynamic ETA → 가장 현실적인 경로
```

> **[v0.2]** 후보 열차는 **B역에 정차하는** 열차만 (종착역이 B 이전이거나, 급행이 B 를 통과하면 제외).
> 사용자가 A역까지 걸어오는 시간(`walkToPlatformSeconds`)을 입력받아 "놓칠 확률"이 높은 열차는 뒤로 미룬다.

## 33. A → B Calculation

```
TotalTravelTime = WaitTimeAtOrigin + TrainTravelTime(A→B, 예측) + FutureDelayAdjustments
```

> **[v0.2]** 원안의 `+ CurrentDelay` 제거 — 열차의 A역 ETA 에 이미 반영된다 (§14).

## 34. Example (수정)

```
현재시각: 08:15    DMC → 왕십리
K5123 DMC ETA: 08:18 → 승차 예상 08:19
DMC→왕십리 예측 주행: 23분 (역사적 median 21분 + conflict +2분)
예상 왕십리 도착: 08:42
```

출력: `약 27분 / 예상 도착 08:42 / 평소보다 +2분`

## 35. Prediction Data Features (ML 단계)

current station, previous station, direction, destination, current delay,
hour, minute, weekday, weekend, holiday, segment,
actual previous segment travel time, previous dwell time,
preceding train gap, preceding train delay, following train gap,
nearby KTX / KTX-Eum / ITX / conventional train count,
conflict probability, priority difference,
historical segment median, historical dwell median,
current abnormal stop score, train sequence changed, rotation delay,
**[v0.2] observation staleness (마지막 관측 후 경과초), polling interval**

## 36. Weather Features

temperature, rain, rain intensity, lightning, wind, heat warning.
`비 = +3분` 처럼 직접 더하지 않는다. incident probability / model feature 로만 쓴다.

## 37. Database

PostgreSQL. 시계열 규모가 커지면 TimescaleDB 고려.

## 38. Initial Tables

`stations`, `segments`, `trains`, `train_observations`, `train_station_events`, `segment_runs`, `station_dwells`, `train_conflicts`, `delay_events`, `train_rotations`, `eta_predictions`
**[v0.2] 추가:** `raw_api_responses`(HTTP 응답 원문), `route_patterns`, `route_pattern_stations`, `station_aliases`, `collector_runs`

실제 DDL: [`db/schema.sql`](../db/schema.sql)

## 39. train_observations

```sql
CREATE TABLE train_observations (
    id BIGSERIAL PRIMARY KEY,
    train_id VARCHAR(50) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    source_generated_at TIMESTAMPTZ,
    station_id VARCHAR(50),
    status VARCHAR(30),
    direction VARCHAR(30),
    destination VARCHAR(100),
    raw_payload JSONB
);
```

> **[v0.2]** 최종본은 `raw_response_id` 로 원문 응답과 연결하고, `train_no`, `service_date`, `status_code`(원본 코드) 를 추가했다.

## 40. train_station_events

`APPROACH`, `ARRIVAL`, `DEPARTURE`, `PASS`

```json
{ "trainId": "K5123", "station": "왕십리", "event": "DEPARTURE", "timestamp": "08:31:24" }
```

> **[v0.2]** 폴링 방식에서는 이벤트의 정확한 시각을 알 수 없다. "이전 관측(아직 이 상태 아님) ~ 이번 관측(이 상태)" 사이 어딘가다.
> 그래서 이벤트마다 `time_lower`, `time_upper`, `estimated_time` 을 저장하고, 중간 상태를 건너뛰어 **추론한 이벤트**는 `inferred=true` 로 표시한다.
> 통계에는 불확실성이 작은 이벤트만 쓴다.

## 41. Segment Run

```ts
interface SegmentRun {
  trainId: string;
  fromStationId: string;
  toStationId: string;
  departureTime: Date;
  arrivalTime: Date;
  travelSeconds: number;
  scheduledSeconds?: number;
  delayDeltaSeconds?: number;
  uncertaintySeconds: number; // [v0.2]
}
```

## 42. Station Dwell

```ts
interface StationDwell {
  trainId: string;
  stationId: string;
  arrivalTime: Date;
  departureTime: Date;
  dwellSeconds: number;
  uncertaintySeconds: number; // [v0.2]
}
```

## 43. ETA Evaluation

```ts
interface ETAPredictionEvaluation {
  predictedAt: Date;
  predictedArrival: Date;
  actualArrival: Date;
  absoluteErrorSeconds: number;
  horizonSeconds: number;
}
```

## 44. Metrics

MAE, Median Absolute Error, P80 Error, P95 Error.
Horizon 별로 따로: `0~5분`, `5~10분`, `10~20분`, `20~40분`, `40분+`.
**[v0.2]** 예측 범위 적중률(coverage: 실제값이 [lower, upper] 안에 들어간 비율)도 같이 본다. 범위가 너무 좁거나 넓으면 보정한다.

## 45. Product Success Metric (초기 목표)

| Horizon | Median Error 목표 |
|---|---|
| 5분 이내 | < 60 sec |
| 10분 | < 90 sec |
| 20분 | < 180 sec |

실제 데이터에 따라 조정한다. "실제 도착" 자체가 폴링 오차(±폴링 간격/2)를 갖는다는 점을 감안한다.

## 46. Backend Architecture

```
Collector → Event Processor → Train State Engine → Conflict Engine → ETA Engine → API → Frontend
```

## 47. Collector

API polling, raw response 저장, failure retry, timestamp normalization, rate limit management.

## 48. Event Processor

raw observation → train arrived / departed / passed, segment entered / exited, abnormal stop.

## 49. Train State Engine

```json
{
  "trainId": "K5123",
  "currentStation": "왕십리",
  "previousStation": "응봉",
  "status": "DEPARTED",
  "delaySeconds": 134,
  "previousSegmentSeconds": 193,
  "precedingTrainId": "K5121",
  "gapSeconds": 210
}
```

## 50. Conflict Engine

```
Target Train + Other Trains + Track/Segment + Predicted Entry Time + Priority Score + Historical Pattern
= Conflict Probability
```

## 51. ETA Engine

```
Base statistical ETA → Current delay effect → Preceding train correction → Conflict correction
→ Abnormal stop correction → Rotation correction → Confidence calculation → Final ETA
```

## 52. Suggested Technology Stack

- Backend: Python, FastAPI
- Data processing: Python, Pandas / Polars
- Database: PostgreSQL (TimescaleDB, Redis optional)
- ML: LightGBM, XGBoost, scikit-learn
- Frontend: Next.js, TypeScript
- Deployment: Docker

## 53. API Endpoints

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/trains` | 현재 열차 목록 |
| GET | `/api/trains/{trainId}` | 특정 열차 |
| GET | `/api/stations/{stationId}/arrivals` | 역 도착 예정 |
| GET | `/api/routes/eta?from=홍대입구&to=구리` | A→B ETA |
| GET | `/api/trains/{trainId}/conflicts` | 간섭 후보 |

```json
{
  "station": "상봉",
  "arrivals": [
    { "trainId": "K5123", "destination": "용문", "etaSeconds": 420, "range": [350, 510], "delaySeconds": 120 }
  ]
}
```

## 54. Frontend MVP

첫 화면은 복잡한 관제 UI 가 아니다. 사용자가 원하는 건 세 가지:
**내 열차가 언제 오는가? / 목적지까지 얼마나 걸리는가? / 왜 늦는가?**

## 55. Station Screen

```
상봉역
용문행  약 5분 후  현재 2정거장 전  +2분 지연
문산행  약 9분 후  현재 4정거장 전  정상운행
```

## 56. Train Screen

```
K5123 용문행
현재: 왕십리 → 청량리
청량리 4분 / 회기 7분 / 상봉 13분 / 구리 20분
현재 예상 지연: +3분
```

## 57. Delay Explanation

```
지연 요인
선행 열차 영향              +1분
간선열차 운행 영향 가능성      +2분
```

공식 확인되지 않은 원인은 반드시 "가능성", "추정" 으로 표현한다.

## 58. Raw Data First

첫 성공조건은 예쁜 앱이 아니라:

> 경의중앙선 열차 1대를 출발부터 종착까지 끊김 없이 추적하고, 모든 역의 도착 / 출발 시각을 DB 에 저장하는 것.

## 59~64. Development Milestones

| # | 내용 | 완료 기준 |
|---|---|---|
| M1 | API client, observation 저장, 열차번호별 상태 추적, ARRIVAL/DEPARTURE 이벤트, segment travel time, dwell time (UI 없음) | §58 달성 |
| M2 | Historical Median ETA = 현재 위치 + segment median + dwell median. **baseline 으로 고정** | 모든 예측 저장 + horizon 별 평가 |
| M3 | Current delay effect + Preceding train | baseline 대비 개선 |
| M4 | KTX / ITX / 일반열차 추적 + Conflict Detector | 데이터 확보 후 |
| M5 | A → B Dynamic ETA | |
| M6 | ML — `ML model > baseline` 검증 시에만 production | |

## 65. What NOT To Do

- 처음부터 AI 모델 개발
- 처음부터 전체 수도권 철도 구현
- 모든 사고유형 구현
- 지도 UI 먼저 개발
- KTX 가 근처라는 이유만으로 지연값 추가
- 공식정보 없이 지연 원인을 확정 표시
- **[v0.2]** 현재 위치 기반 ETA 에 현재 지연을 또 더하기 (이중 계산)

## 66. Unknowns

1. 각 API 의 실제 호출 제한
2. 열차번호가 API 들 사이에서 동일하게 매칭 가능한지
3. KTX / ITX / 일반열차의 충분한 실시간 위치정보가 있는지
4. 공개정보만으로 대피역 / 추월 가능역을 정확히 모델링할 수 있는지
5. 실제 차량운용 연결관계를 추정할 수 있는지
6. 화물열차 실시간 정보 접근 가능 여부
7. 신호대기 원인정보가 공개되는지
8. **[v0.2]** `recptnDt` 의 갱신 주기 (의미는 도착정보 명세서에서 "정보 생성 시각"으로 확인됨)
9. **[v0.2]** `updnLine` 0/1 이 경의중앙선에서 어느 방향(문산/용문)인지
10. **[v0.2]** `trainSttus` 가 모든 역에서 0→1→2(→3) 순서로 빠짐없이 나오는지, 급행 통과역은 어떻게 나오는지
11. **[v0.2]** 경의중앙선 공식 시간표를 기계가 읽을 수 있는 형태로 얻을 수 있는지 (없으면 "시간표 대비 지연" 계산 불가)
12. **[v0.2]** 실시간 API 가 **서울시 밖 역**(문산·일산·구리·덕소·양평·용문 등)도 제공하는지. 도착정보 명세서는 미제공이라고 명시함

확인되지 않은 데이터는 존재한다고 가정하지 않는다.

## 67. Source Reliability Rule

1. 코레일 / 국가철도공단 공식 데이터
2. 서울시 / 공공데이터포털
3. 공식 시간표
4. 실제 수집 Observation
5. 철도 관련 연구자료
6. 기타 참고자료
7. 커뮤니티 / 나무위키 — 아이디어 발굴, 사례 탐색, 검색 키워드 발굴 용도로만

## 68. Train Priority Important Note

실제 운전취급은 운행 시각표, 현재 지연, 열차 종류, 열차 간격, 교행 위치, 대피 가능 여부, 선로 상황, 관제 판단, 안전상황에 따라 달라진다.
**Priority != Guaranteed Right of Way.** Priority 는 prediction feature 다.

## 69. Long-Term Vision

```
08:31:20
K5123 왕십리 출발
current delay: +61 sec
Ahead train: K5121, gap 185 sec
KTX-Eum: 청량리 동쪽 접근
Conflict probability: 72%
Predicted additional delay: +96 sec
상봉 ETA: 08:44
Confidence: 78%
```

사용자에게는:

```
상봉역 용문행
약 12분 후
평소보다 약 3분 지연
간선열차 운행 및 선행열차 영향 가능성
```

## 70. Final Product Definition

경의중앙선과 주변 간선열차의 실시간 운행 상태를 통합 분석해서, 열차 간 상호작용과 지연 전파를 반영한 **역별 도착 ETA** 와 **A→B 실시간 예상 소요시간**을 제공하는 철도 운행 예측 시스템.

## 71. Claude Development Instruction

1. 확인되지 않은 API 나 필드를 임의로 만들지 않는다.
2. 먼저 실제 API 응답 schema 를 확인한다.
3. 모든 외부 API raw payload 를 보존한다.
4. 데이터 수집기부터 구현한다.
5. 열차 상태 추적이 안정화되기 전에는 ML 을 쓰지 않는다.
6. 항상 baseline ETA 와 새 모델을 비교한다.
7. 추론한 지연 원인과 공식 확인된 지연 원인을 구분한다.
8. Train Priority 를 절대적인 운행 규칙으로 하드코딩하지 않는다.
9. 알고리즘은 테스트 가능한 작은 모듈로 나눈다.
10. 모든 ETA 예측은 나중에 실제 도착시각과 비교할 수 있도록 저장한다.
