-- TCCS operational call history.
-- Safe to run repeatedly on an existing PostgreSQL database.

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

CREATE INDEX IF NOT EXISTS idx_call_history_originated_at ON call_history(originated_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_history_target_station ON call_history(target_station_id);
CREATE INDEX IF NOT EXISTS idx_call_history_status ON call_history(status);
