-- TCCS Stage 2: migrate the pre-existing station_group_members schema.
-- Older deployments used group_id; current application code uses station_group_id.
-- This is intentionally idempotent and preserves existing membership rows.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'station_group_members'
          AND column_name = 'group_id'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'station_group_members'
          AND column_name = 'station_group_id'
    ) THEN
        ALTER TABLE public.station_group_members
            RENAME COLUMN group_id TO station_group_id;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_station_group_members_station
    ON public.station_group_members(station_id);
