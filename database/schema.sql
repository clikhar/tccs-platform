-- TCCS Controller application schema.
-- PostgreSQL is the development database; application models are the runtime source of truth.

CREATE TABLE IF NOT EXISTS sections (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS controllers (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    section_id BIGINT REFERENCES sections(id),
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS stations (
    id BIGSERIAL PRIMARY KEY,
    station_number VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    location VARCHAR(256),
    section_id BIGINT NOT NULL REFERENCES sections(id),
    sip_extension VARCHAR(64) NOT NULL UNIQUE,
    station_type VARCHAR(32) NOT NULL DEFAULT 'WAY_STATION',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    priority INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS station_groups (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    section_id BIGINT REFERENCES sections(id),
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS station_group_members (
    group_id BIGINT NOT NULL REFERENCES station_groups(id) ON DELETE CASCADE,
    station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, station_id)
);

CREATE TABLE IF NOT EXISTS call_history (
    id BIGSERIAL PRIMARY KEY,
    call_type VARCHAR(32) NOT NULL,
    source_extension VARCHAR(64) NOT NULL DEFAULT '9999',
    target_station_id BIGINT REFERENCES stations(id) ON DELETE SET NULL,
    target_station_number VARCHAR(32),
    target_name VARCHAR(128),
    group_code VARCHAR(32),
    status VARCHAR(32) NOT NULL DEFAULT 'ORIGINATED',
    originated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER
);

CREATE INDEX IF NOT EXISTS idx_stations_section ON stations(section_id);
CREATE INDEX IF NOT EXISTS idx_groups_section ON station_groups(section_id);
CREATE INDEX IF NOT EXISTS idx_call_history_originated_at ON call_history(originated_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_history_target_station ON call_history(target_station_id);
CREATE INDEX IF NOT EXISTS idx_call_history_status ON call_history(status);
