-- Gyeongui-Jungang Real-Time ETA Predictor — Phase 1 schema (PostgreSQL 14+)
-- See docs/DESIGN.md §4. Everything below raw_api_responses must be rebuildable from it.

-- ---------------------------------------------------------------------------
-- Static reference data
-- ---------------------------------------------------------------------------

CREATE TABLE stations (
    station_code    VARCHAR(50) PRIMARY KEY,         -- internal stable code
    name            VARCHAR(100) NOT NULL,           -- canonical Korean name
    api_station_id  VARCHAR(50),                     -- statnId from Seoul API (TODO: fill from real payloads)
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    can_overtake    BOOLEAN,                         -- NULL = UNKNOWN
    can_turnback    BOOLEAN                          -- NULL = UNKNOWN
);

CREATE TABLE station_aliases (
    alias           VARCHAR(100) PRIMARY KEY,        -- normalized name as seen in a source
    station_code    VARCHAR(50) NOT NULL REFERENCES stations(station_code),
    source          VARCHAR(50) NOT NULL             -- e.g. 'seoul_realtime', 'manual'
);

CREATE TABLE route_patterns (
    pattern_id      VARCHAR(50) PRIMARY KEY,         -- 'MAIN', 'SEOUL_BRANCH'
    description     TEXT
);

CREATE TABLE route_pattern_stations (
    pattern_id      VARCHAR(50) NOT NULL REFERENCES route_patterns(pattern_id),
    seq             INTEGER NOT NULL,
    station_code    VARCHAR(50) NOT NULL REFERENCES stations(station_code),
    PRIMARY KEY (pattern_id, seq),
    UNIQUE (pattern_id, station_code)
);

-- ---------------------------------------------------------------------------
-- Raw collection (never deleted)
-- ---------------------------------------------------------------------------

CREATE TABLE collector_runs (
    id              BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMPTZ NOT NULL,
    stopped_at      TIMESTAMPTZ,
    poll_interval_s INTEGER NOT NULL,
    version         VARCHAR(50) NOT NULL
);

CREATE TABLE raw_api_responses (
    id              BIGSERIAL PRIMARY KEY,
    collector_run_id BIGINT REFERENCES collector_runs(id),
    source          VARCHAR(50) NOT NULL,            -- 'seoul_realtime_position'
    request_url     TEXT NOT NULL,                   -- API key must be masked before storing
    requested_at    TIMESTAMPTZ NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL,            -- when the response was received
    latency_ms      INTEGER,
    http_status     INTEGER,
    api_code        VARCHAR(30),                     -- e.g. 'INFO-000', 'INFO-200'
    record_count    INTEGER,
    body            JSONB,                           -- parsed JSON body, or NULL if not JSON
    body_text       TEXT,                            -- kept only when body is not valid JSON
    error           TEXT
);
CREATE INDEX raw_api_responses_observed_at_idx ON raw_api_responses (observed_at);

CREATE TABLE train_observations (
    id                   BIGSERIAL PRIMARY KEY,
    raw_response_id      BIGINT NOT NULL REFERENCES raw_api_responses(id),
    service_date         DATE NOT NULL,
    train_no             VARCHAR(20) NOT NULL,
    train_run_id         VARCHAR(50) NOT NULL,       -- '{service_date}:{train_no}'
    observed_at          TIMESTAMPTZ NOT NULL,
    source_generated_at  TIMESTAMPTZ,                -- recptnDt (meaning UNKNOWN, see DESIGN U2)
    api_station_id       VARCHAR(50),
    station_name         VARCHAR(100),
    station_code         VARCHAR(50),                -- NULL if name not resolved
    terminal_station_name VARCHAR(100),
    updn_line            VARCHAR(10),                -- raw code
    status_code          VARCHAR(10),                -- raw trainSttus
    status               VARCHAR(30) NOT NULL,       -- APPROACHING/ARRIVED/DEPARTED/PREV_DEPARTED/UNKNOWN
    express_code         VARCHAR(10),                -- raw directAt
    is_last_train        BOOLEAN,
    raw_payload          JSONB NOT NULL
);
CREATE INDEX train_observations_run_idx ON train_observations (train_run_id, observed_at);
CREATE INDEX train_observations_observed_at_idx ON train_observations (observed_at);

-- ---------------------------------------------------------------------------
-- Derived tracking data (rebuildable; tagged with processor_version)
-- ---------------------------------------------------------------------------

CREATE TABLE train_runs (
    train_run_id         VARCHAR(50) PRIMARY KEY,
    service_date         DATE NOT NULL,
    train_no             VARCHAR(20) NOT NULL,
    train_type           VARCHAR(30) NOT NULL DEFAULT 'GYEONGUI_JUNGANG',
    pattern_id           VARCHAR(50) REFERENCES route_patterns(pattern_id),
    direction            SMALLINT,                   -- +1 toward 지평/용문, -1 toward 문산/서울역
    terminal_station_code VARCHAR(50),
    is_express           BOOLEAN,
    first_seen_at        TIMESTAMPTZ,
    last_seen_at         TIMESTAMPTZ,
    has_gap              BOOLEAN NOT NULL DEFAULT FALSE,
    processor_version    VARCHAR(20) NOT NULL
);

CREATE TABLE train_station_events (
    id                   BIGSERIAL PRIMARY KEY,
    train_run_id         VARCHAR(50) NOT NULL REFERENCES train_runs(train_run_id),
    station_code         VARCHAR(50) NOT NULL REFERENCES stations(station_code),
    event_type           VARCHAR(20) NOT NULL,       -- APPROACH / ARRIVAL / DEPARTURE / PASS
    time_lower           TIMESTAMPTZ NOT NULL,       -- last observation before the event
    time_upper           TIMESTAMPTZ NOT NULL,       -- first observation after the event
    estimated_time       TIMESTAMPTZ NOT NULL,
    inferred             BOOLEAN NOT NULL,           -- milestone was skipped between polls
    processor_version    VARCHAR(20) NOT NULL,
    UNIQUE (train_run_id, station_code, event_type, processor_version)
);

CREATE TABLE segment_runs (
    id                   BIGSERIAL PRIMARY KEY,
    train_run_id         VARCHAR(50) NOT NULL REFERENCES train_runs(train_run_id),
    from_station_code    VARCHAR(50) NOT NULL REFERENCES stations(station_code),
    to_station_code      VARCHAR(50) NOT NULL REFERENCES stations(station_code),
    direction            SMALLINT NOT NULL,          -- relative to pattern_id order
    departure_time       TIMESTAMPTZ NOT NULL,
    arrival_time         TIMESTAMPTZ NOT NULL,
    travel_seconds       DOUBLE PRECISION NOT NULL,
    uncertainty_seconds  DOUBLE PRECISION NOT NULL,
    usable               BOOLEAN NOT NULL,
    scheduled_seconds    DOUBLE PRECISION,           -- NULL until timetable (D5) exists
    processor_version    VARCHAR(20) NOT NULL
);
CREATE INDEX segment_runs_key_idx ON segment_runs (from_station_code, to_station_code, departure_time);

CREATE TABLE station_dwells (
    id                   BIGSERIAL PRIMARY KEY,
    train_run_id         VARCHAR(50) NOT NULL REFERENCES train_runs(train_run_id),
    station_code         VARCHAR(50) NOT NULL REFERENCES stations(station_code),
    next_station_code    VARCHAR(50) NOT NULL REFERENCES stations(station_code), -- heading; pattern-independent direction
    arrival_time         TIMESTAMPTZ NOT NULL,
    departure_time       TIMESTAMPTZ NOT NULL,
    dwell_seconds        DOUBLE PRECISION NOT NULL,
    uncertainty_seconds  DOUBLE PRECISION NOT NULL,
    usable               BOOLEAN NOT NULL,
    processor_version    VARCHAR(20) NOT NULL
);
CREATE INDEX station_dwells_key_idx ON station_dwells (station_code, next_station_code, arrival_time);

-- Aggregated statistics per bucket level (L0/L1/L2, see DESIGN §6.3)
CREATE TABLE travel_stats (
    kind                 VARCHAR(10) NOT NULL,       -- 'SEGMENT' | 'DWELL'
    from_station_code    VARCHAR(50) NOT NULL,
    to_station_code      VARCHAR(50) NOT NULL,       -- DWELL: next station (heading)
    day_type             VARCHAR(20) NOT NULL,       -- '*' for any
    time_band            VARCHAR(20) NOT NULL,       -- '*' for any
    n                    INTEGER NOT NULL,
    p20                  DOUBLE PRECISION NOT NULL,
    p50                  DOUBLE PRECISION NOT NULL,
    p80                  DOUBLE PRECISION NOT NULL,
    p95                  DOUBLE PRECISION NOT NULL,
    computed_at          TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (kind, from_station_code, to_station_code, day_type, time_band)
);

-- ---------------------------------------------------------------------------
-- Predictions and evaluation (SPEC §43-44)
-- ---------------------------------------------------------------------------

CREATE TABLE eta_predictions (
    id                   BIGSERIAL PRIMARY KEY,
    model_version        VARCHAR(30) NOT NULL,       -- 'baseline-v0', ...
    train_run_id         VARCHAR(50) NOT NULL,
    target_station_code  VARCHAR(50) NOT NULL,
    predicted_at         TIMESTAMPTZ NOT NULL,
    predicted_arrival    TIMESTAMPTZ NOT NULL,
    eta_seconds          DOUBLE PRECISION NOT NULL,
    lower_seconds        DOUBLE PRECISION NOT NULL,
    upper_seconds        DOUBLE PRECISION NOT NULL,
    confidence           DOUBLE PRECISION NOT NULL,
    factors              JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX eta_predictions_lookup_idx ON eta_predictions (train_run_id, target_station_code);

CREATE TABLE eta_evaluations (
    prediction_id        BIGINT PRIMARY KEY REFERENCES eta_predictions(id),
    actual_arrival       TIMESTAMPTZ NOT NULL,
    actual_uncertainty_seconds DOUBLE PRECISION NOT NULL,
    absolute_error_seconds DOUBLE PRECISION NOT NULL,
    signed_error_seconds DOUBLE PRECISION NOT NULL,  -- predicted - actual
    horizon_seconds      DOUBLE PRECISION NOT NULL,
    within_range         BOOLEAN NOT NULL
);

-- ---------------------------------------------------------------------------
-- Phase 2+ placeholders (no writer yet)
-- ---------------------------------------------------------------------------

CREATE TABLE delay_events (
    id                   BIGSERIAL PRIMARY KEY,
    train_run_id         VARCHAR(50) NOT NULL,
    cause                VARCHAR(40) NOT NULL,       -- DelayCause
    confirmed            BOOLEAN NOT NULL DEFAULT FALSE,
    confidence           DOUBLE PRECISION,
    station_code         VARCHAR(50),
    started_at           TIMESTAMPTZ,
    ended_at             TIMESTAMPTZ,
    source               VARCHAR(50),
    detail               JSONB
);

CREATE TABLE train_conflicts (
    id                   BIGSERIAL PRIMARY KEY,
    target_train_run_id  VARCHAR(50) NOT NULL,
    related_train_id     VARCHAR(50) NOT NULL,
    target_priority      INTEGER,
    related_priority     INTEGER,
    from_station_code    VARCHAR(50),
    to_station_code      VARCHAR(50),
    expected_target_entry  TIMESTAMPTZ,
    expected_related_entry TIMESTAMPTZ,
    conflict_probability DOUBLE PRECISION,
    expected_delay_seconds DOUBLE PRECISION,
    confidence           DOUBLE PRECISION,
    computed_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE train_rotations (
    inbound_train_run_id  VARCHAR(50) NOT NULL,
    outbound_train_run_id VARCHAR(50) NOT NULL,
    confirmed             BOOLEAN NOT NULL DEFAULT FALSE,
    scheduled_turnaround_seconds DOUBLE PRECISION,
    predicted_inbound_delay_seconds DOUBLE PRECISION,
    predicted_outbound_delay_seconds DOUBLE PRECISION,
    PRIMARY KEY (inbound_train_run_id, outbound_train_run_id)
);
