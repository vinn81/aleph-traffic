CREATE TABLE IF NOT EXISTS traffic_daily (
    local_date DATE PRIMARY KEY,
    captured_at TIMESTAMPTZ NOT NULL,
    road_name TEXT NOT NULL,
    average_speed DOUBLE PRECISION NOT NULL CHECK (average_speed > 0 AND average_speed <= 200),
    link_count INTEGER NOT NULL CHECK (link_count > 0),
    min_speed DOUBLE PRECISION CHECK (min_speed > 0 AND min_speed <= 200),
    max_speed DOUBLE PRECISION CHECK (max_speed > 0 AND max_speed <= 200),
    source_updated_at TIMESTAMPTZ,
    source_status TEXT NOT NULL CHECK (source_status IN ('NORMAL', 'DELAYED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (min_speed IS NULL OR max_speed IS NULL OR min_speed <= max_speed),
    CHECK (min_speed IS NULL OR average_speed >= min_speed),
    CHECK (max_speed IS NULL OR average_speed <= max_speed)
);

CREATE TABLE IF NOT EXISTS collection_attempts (
    id BIGSERIAL PRIMARY KEY,
    attempted_at TIMESTAMPTZ NOT NULL,
    local_date DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('NORMAL', 'DELAYED', 'FAILED')),
    message TEXT,
    average_speed DOUBLE PRECISION,
    link_count INTEGER,
    source_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attempts_date_time
ON collection_attempts(local_date, attempted_at DESC);
