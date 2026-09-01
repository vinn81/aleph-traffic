CREATE TABLE IF NOT EXISTS traffic_daily (
    local_date DATE PRIMARY KEY,
    captured_at TIMESTAMPTZ NOT NULL,
    road_name TEXT NOT NULL,
    average_speed DOUBLE PRECISION NOT NULL,
    link_count INTEGER NOT NULL,
    min_speed DOUBLE PRECISION,
    max_speed DOUBLE PRECISION,
    source_updated_at TIMESTAMPTZ,
    source_status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS collection_attempts (
    id BIGSERIAL PRIMARY KEY,
    attempted_at TIMESTAMPTZ NOT NULL,
    local_date DATE NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    average_speed DOUBLE PRECISION,
    link_count INTEGER,
    source_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attempts_date_time
ON collection_attempts(local_date, attempted_at DESC);
