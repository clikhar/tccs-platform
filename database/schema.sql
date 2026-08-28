-- Initial TCCS Controller data model.
-- This is intentionally provider-neutral SQL; migrations will formalize deployment later.

CREATE TABLE IF NOT EXISTS sections (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS controllers (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    section_id BIGINT REFERENCES sections(id),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS station_groups (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    section_id BIGINT REFERENCES sections(id),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS station_group_members (
    group_id BIGINT NOT NULL REFERENCES station_groups(id) ON DELETE CASCADE,
    station_id BIGINT NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, station_id)
);

CREATE INDEX IF NOT EXISTS idx_stations_section ON stations(section_id);
CREATE INDEX IF NOT EXISTS idx_groups_section ON station_groups(section_id);
