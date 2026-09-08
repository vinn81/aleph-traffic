-- 달구벌 NOW / Neon PostgreSQL schema
-- 기존 DB에 다시 실행해도 데이터를 삭제하지 않는 방향으로 작성되어 있습니다.

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
    status TEXT NOT NULL CHECK (status IN ('NORMAL', 'DELAYED', 'FAILED', 'MISSED')),
    failure_type TEXT,
    message TEXT,
    average_speed DOUBLE PRECISION,
    link_count INTEGER,
    source_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 기존 collection_attempts 테이블에도 안전하게 컬럼/상태 제약을 갱신합니다.
ALTER TABLE collection_attempts
ADD COLUMN IF NOT EXISTS failure_type TEXT;

-- 기존 인라인 CHECK 이름과 새 명시적 CHECK 이름을 모두 정리한 뒤 MISSED를 허용합니다.
ALTER TABLE collection_attempts
DROP CONSTRAINT IF EXISTS collection_attempts_status_check;

ALTER TABLE collection_attempts
DROP CONSTRAINT IF EXISTS chk_collection_attempt_status;

ALTER TABLE collection_attempts
ADD CONSTRAINT chk_collection_attempt_status
CHECK (status IN ('NORMAL', 'DELAYED', 'FAILED', 'MISSED'));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_failure_type'
          AND conrelid = 'collection_attempts'::regclass
    ) THEN
        ALTER TABLE collection_attempts
        ADD CONSTRAINT chk_failure_type
        CHECK (
            failure_type IS NULL
            OR failure_type IN (
                'TIMEOUT',
                'HTTP_ERROR',
                'INVALID_JSON',
                'EMPTY_DATA',
                'STALE_DATA'
            )
        );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_attempts_date_time
ON collection_attempts(local_date, attempted_at DESC);

-- Cron이 재시도되어도 날짜별 MISSED 행은 한 건만 유지합니다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_missed_per_day
ON collection_attempts(local_date)
WHERE status = 'MISSED';

-- 실제 공개 원천값을 확인할 수 있도록 매일 최대 5개 링크 샘플을 저장합니다.
-- API 키/비밀값/DB 접속정보는 저장하지 않습니다.
CREATE TABLE IF NOT EXISTS traffic_source_samples (
    id BIGSERIAL PRIMARY KEY,
    local_date DATE NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    road_name TEXT NOT NULL,
    link_id TEXT,
    speed DOUBLE PRECISION NOT NULL CHECK (speed > 0 AND speed <= 200),
    source_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_source_sample_daily
        FOREIGN KEY (local_date)
        REFERENCES traffic_daily(local_date)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_source_samples_date
ON traffic_source_samples(local_date, id);
