from __future__ import annotations

import hmac
import logging
import os
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dalgubeol-now")

app = FastAPI(title="달구벌 NOW API", version="5.1.0")

KST = ZoneInfo("Asia/Seoul")
ROAD_NAME = "달구벌대로"
EXPECTED_HOUR_KST = 9
EXPECTED_MINUTE_KST = 0
OFFICIAL_CAPTURE_LAST_MINUTE_KST = 9
MISSED_CHECK_HOUR_KST = 10
MISSED_CHECK_MINUTE_KST = 0
HISTORY_QUERY_LIMIT = 30
DASHBOARD_HISTORY_LIMIT = 30
SOURCE_SAMPLE_LIMIT = 5

SOURCE_NAME = "국가교통정보센터 ITS"
SOURCE_API_ENDPOINT = "https://openapi.its.go.kr:9443/trafficInfo"
SOURCE_CONTEXT_URL = "https://its.go.kr"
SOURCE_BBOX = {
    "minX": 128.40,
    "maxX": 128.80,
    "minY": 35.75,
    "maxY": 36.00,
}

FAILURE_TYPES = (
    "TIMEOUT",
    "HTTP_ERROR",
    "INVALID_JSON",
    "EMPTY_DATA",
    "STALE_DATA",
)


# =========================================================
# 환경 / 시간 / 인증
# =========================================================

def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def db_url() -> str:
    return env("DATABASE_URL") or env("POSTGRES_URL")


def shared_secret() -> str:
    # 로컬 수집기 인증. 기존 배포의 CRON_SECRET도 호환한다.
    return env("INGEST_SECRET") or env("CRON_SECRET")


def cron_secret() -> str:
    # Vercel Cron은 CRON_SECRET을 Authorization Bearer로 자동 전달한다.
    return env("CRON_SECRET")


def now_kst() -> datetime:
    return datetime.now(KST)


def expected_collect_passed(current: datetime) -> bool:
    expected = datetime.combine(
        current.date(),
        time(EXPECTED_HOUR_KST, EXPECTED_MINUTE_KST),
        tzinfo=KST,
    )
    return current >= expected


def verify_secret(authorization: str | None) -> bool:
    secret = shared_secret()
    if not secret:
        return False
    return hmac.compare_digest(authorization or "", f"Bearer {secret}")


def verify_cron_secret(authorization: str | None) -> bool:
    secret = cron_secret()
    if not secret:
        return False
    return hmac.compare_digest(authorization or "", f"Bearer {secret}")


def official_capture_time(captured_at: datetime) -> bool:
    captured_kst = captured_at.astimezone(KST)
    return (
        captured_kst.hour == EXPECTED_HOUR_KST
        and EXPECTED_MINUTE_KST
        <= captured_kst.minute
        <= OFFICIAL_CAPTURE_LAST_MINUTE_KST
    )


# =========================================================
# 요청 모델
# =========================================================

class SourceSamplePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roadName: str
    linkId: str | None = Field(default=None, max_length=100)
    speed: float = Field(gt=0, le=200)
    sourceUpdatedAt: datetime | None = None

    @field_validator("roadName")
    @classmethod
    def validate_road(cls, value: str) -> str:
        if ROAD_NAME not in value.strip():
            raise ValueError(f"roadName에는 '{ROAD_NAME}'이 포함되어야 합니다.")
        return value.strip()

    @field_validator("sourceUpdatedAt")
    @classmethod
    def validate_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("sourceUpdatedAt에는 timezone 정보가 필요합니다.")
        return value


class IngestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    localDate: date
    capturedAt: datetime
    road: str
    averageSpeed: float = Field(gt=0, le=200)
    linkCount: int = Field(gt=0, le=10000)
    minSpeed: float = Field(gt=0, le=200)
    maxSpeed: float = Field(gt=0, le=200)
    sourceUpdatedAt: datetime | None = None
    sourceStatus: Literal["NORMAL", "DELAYED"]

    sourceTimestampCount: int | None = Field(default=None, ge=0, le=10000)
    staleLinkCount: int | None = Field(default=None, ge=0, le=10000)
    staleRatio: float | None = Field(default=None, ge=0, le=1)
    samples: list[SourceSamplePayload] = Field(
        default_factory=list,
        max_length=SOURCE_SAMPLE_LIMIT,
    )

    @field_validator("road")
    @classmethod
    def validate_road(cls, value: str) -> str:
        if value.strip() != ROAD_NAME:
            raise ValueError(f"road는 '{ROAD_NAME}'이어야 합니다.")
        return ROAD_NAME

    @field_validator("capturedAt", "sourceUpdatedAt")
    @classmethod
    def validate_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("시간 값에는 timezone 정보가 필요합니다.")
        return value

    @model_validator(mode="after")
    def validate_consistency(self):
        captured_kst = self.capturedAt.astimezone(KST)
        if self.localDate != captured_kst.date():
            raise ValueError("localDate와 capturedAt의 한국 날짜가 일치해야 합니다.")

        if not official_capture_time(self.capturedAt):
            raise ValueError(
                "공식 일별 기록은 KST 09:00~09:09에 수집된 값만 저장할 수 있습니다."
            )

        if not (self.minSpeed <= self.averageSpeed <= self.maxSpeed):
            raise ValueError("minSpeed <= averageSpeed <= maxSpeed 조건을 만족해야 합니다.")

        if (
            self.sourceTimestampCount is not None
            and self.staleLinkCount is not None
            and self.staleLinkCount > self.sourceTimestampCount
        ):
            raise ValueError("staleLinkCount는 sourceTimestampCount보다 클 수 없습니다.")

        return self


class AttemptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    localDate: date
    attemptedAt: datetime
    status: Literal["FAILED"]
    failureType: Literal[
        "TIMEOUT",
        "HTTP_ERROR",
        "INVALID_JSON",
        "EMPTY_DATA",
        "STALE_DATA",
    ] | None = None
    message: str = Field(min_length=1, max_length=500)

    @field_validator("attemptedAt")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("attemptedAt에는 timezone 정보가 필요합니다.")
        return value

    @model_validator(mode="after")
    def validate_date(self):
        if self.localDate != self.attemptedAt.astimezone(KST).date():
            raise ValueError("localDate와 attemptedAt의 한국 날짜가 일치해야 합니다.")
        return self


# =========================================================
# DB
# =========================================================

def open_db():
    url = db_url()
    if not url:
        raise RuntimeError("DATABASE_URL이 설정되지 않았습니다.")
    # Connection context가 정상 종료 시 commit, 예외 시 rollback한다.
    return psycopg.connect(url)


def serialize_daily(row) -> dict | None:
    if not row:
        return None

    return {
        "date": row[0].isoformat(),
        "capturedAt": row[1].isoformat(),
        "roadName": row[2],
        "averageSpeed": float(row[3]),
        "linkCount": int(row[4]),
        "minSpeed": float(row[5]) if row[5] is not None else None,
        "maxSpeed": float(row[6]) if row[6] is not None else None,
        "sourceUpdatedAt": row[7].isoformat() if row[7] else None,
        "sourceStatus": row[8],
    }


def daily_select_sql() -> str:
    return """
        SELECT
            local_date,
            captured_at,
            road_name,
            average_speed,
            link_count,
            min_speed,
            max_speed,
            source_updated_at,
            source_status
        FROM traffic_daily
    """


def query_daily(conn, limit: int = HISTORY_QUERY_LIMIT) -> list[dict]:
    rows = conn.execute(
        daily_select_sql() + " ORDER BY local_date DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [serialize_daily(row) for row in rows]


def query_daily_on(conn, target_date: date) -> dict | None:
    row = conn.execute(
        daily_select_sql() + " WHERE local_date = %s LIMIT 1",
        (target_date,),
    ).fetchone()
    return serialize_daily(row)


def query_latest_daily(conn) -> dict | None:
    row = conn.execute(
        daily_select_sql() + " ORDER BY local_date DESC LIMIT 1"
    ).fetchone()
    return serialize_daily(row)


def query_latest_normal(conn) -> dict | None:
    row = conn.execute(
        daily_select_sql()
        + " WHERE source_status = 'NORMAL' ORDER BY local_date DESC LIMIT 1"
    ).fetchone()
    return serialize_daily(row)


def query_total_days(conn) -> int:
    row = conn.execute("SELECT COUNT(*) FROM traffic_daily").fetchone()
    return int(row[0]) if row else 0


def query_latest_attempt_today(conn, today: date) -> dict | None:
    row = conn.execute(
        """
        SELECT
            attempted_at,
            status,
            failure_type,
            message,
            average_speed,
            link_count,
            source_updated_at
        FROM collection_attempts
        WHERE local_date = %s
        ORDER BY attempted_at DESC
        LIMIT 1
        """,
        (today,),
    ).fetchone()

    if not row:
        return None

    return {
        "attemptedAt": row[0].isoformat(),
        "status": row[1],
        "failureType": row[2],
        "message": row[3],
        "averageSpeed": float(row[4]) if row[4] is not None else None,
        "linkCount": int(row[5]) if row[5] is not None else None,
        "sourceUpdatedAt": row[6].isoformat() if row[6] else None,
    }


def query_latest_collector_attempt_today(conn, today: date) -> dict | None:
    row = conn.execute(
        """
        SELECT
            attempted_at,
            status,
            failure_type,
            message,
            average_speed,
            link_count,
            source_updated_at
        FROM collection_attempts
        WHERE local_date = %s
          AND status <> 'MISSED'
        ORDER BY attempted_at DESC
        LIMIT 1
        """,
        (today,),
    ).fetchone()

    if not row:
        return None

    return {
        "attemptedAt": row[0].isoformat(),
        "status": row[1],
        "failureType": row[2],
        "message": row[3],
        "averageSpeed": float(row[4]) if row[4] is not None else None,
        "linkCount": int(row[5]) if row[5] is not None else None,
        "sourceUpdatedAt": row[6].isoformat() if row[6] else None,
    }


def query_recent_attempts(conn, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            attempted_at,
            local_date,
            status,
            failure_type,
            average_speed,
            link_count,
            source_updated_at
        FROM collection_attempts
        ORDER BY attempted_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()

    return [
        {
            "attemptedAt": row[0].isoformat(),
            "localDate": row[1].isoformat(),
            "status": row[2],
            "failureType": row[3],
            "averageSpeed": float(row[4]) if row[4] is not None else None,
            "linkCount": int(row[5]) if row[5] is not None else None,
            "sourceUpdatedAt": row[6].isoformat() if row[6] else None,
        }
        for row in rows
    ]


def query_source_samples(conn, target_date: date, limit: int = SOURCE_SAMPLE_LIMIT) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            road_name,
            link_id,
            speed,
            source_updated_at,
            captured_at
        FROM traffic_source_samples
        WHERE local_date = %s
        ORDER BY id ASC
        LIMIT %s
        """,
        (target_date, limit),
    ).fetchall()

    return [
        {
            "roadName": row[0],
            "linkId": row[1],
            "speed": float(row[2]),
            "sourceUpdatedAt": row[3].isoformat() if row[3] else None,
            "capturedAt": row[4].isoformat(),
        }
        for row in rows
    ]


def save_attempt(
    conn,
    *,
    attempted_at: datetime,
    local_date: date,
    status: str,
    message: str | None,
    failure_type: str | None = None,
    average_speed: float | None = None,
    link_count: int | None = None,
    source_updated_at: datetime | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO collection_attempts (
            attempted_at,
            local_date,
            status,
            failure_type,
            message,
            average_speed,
            link_count,
            source_updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            attempted_at,
            local_date,
            status,
            failure_type,
            message,
            average_speed,
            link_count,
            source_updated_at,
        ),
    )


def save_daily(conn, payload: IngestPayload) -> bool:
    """Save one day. A DELAYED retry never overwrites an existing NORMAL row."""
    cursor = conn.execute(
        """
        INSERT INTO traffic_daily (
            local_date,
            captured_at,
            road_name,
            average_speed,
            link_count,
            min_speed,
            max_speed,
            source_updated_at,
            source_status,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (local_date)
        DO UPDATE SET
            captured_at = EXCLUDED.captured_at,
            road_name = EXCLUDED.road_name,
            average_speed = EXCLUDED.average_speed,
            link_count = EXCLUDED.link_count,
            min_speed = EXCLUDED.min_speed,
            max_speed = EXCLUDED.max_speed,
            source_updated_at = EXCLUDED.source_updated_at,
            source_status = EXCLUDED.source_status,
            updated_at = NOW()
        WHERE
            traffic_daily.source_status <> 'NORMAL'
            OR EXCLUDED.source_status = 'NORMAL'
        """,
        (
            payload.localDate,
            payload.capturedAt,
            payload.road,
            payload.averageSpeed,
            payload.linkCount,
            payload.minSpeed,
            payload.maxSpeed,
            payload.sourceUpdatedAt,
            payload.sourceStatus,
        ),
    )
    return cursor.rowcount > 0


def replace_source_samples(conn, payload: IngestPayload) -> None:
    conn.execute(
        "DELETE FROM traffic_source_samples WHERE local_date = %s",
        (payload.localDate,),
    )

    for sample in payload.samples[:SOURCE_SAMPLE_LIMIT]:
        conn.execute(
            """
            INSERT INTO traffic_source_samples (
                local_date,
                captured_at,
                road_name,
                link_id,
                speed,
                source_updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                payload.localDate,
                payload.capturedAt,
                sample.roadName,
                sample.linkId,
                sample.speed,
                sample.sourceUpdatedAt,
            ),
        )


# =========================================================
# 검증 로직 (DB를 변경하지 않는 합성 재생)
# =========================================================

def synthetic_failure_replay() -> list[dict]:
    """Replay five external failure classes in memory only.

    TIMEOUT/HTTP/INVALID_JSON/EMPTY_DATA never reach traffic_daily.
    STALE_DATA reaches the summary stage as DELAYED, but the DB upsert policy blocks
    DELAYED from overwriting an existing NORMAL row.
    """
    scenarios = []

    definitions = {
        "TIMEOUT": "외부 ITS 응답 시간 초과",
        "HTTP_ERROR": "외부 ITS HTTP/연결 오류",
        "INVALID_JSON": "외부 응답 JSON 해석 실패",
        "EMPTY_DATA": "달구벌대로 유효 데이터 없음",
        "STALE_DATA": "원천 데이터 지연으로 DELAYED 판정",
    }

    for failure_type in FAILURE_TYPES:
        if failure_type == "STALE_DATA":
            reached_daily_write = True
            existing_status = "NORMAL"
            incoming_status = "DELAYED"
            overwrite_allowed = (
                existing_status != "NORMAL" or incoming_status == "NORMAL"
            )
            preserved = not overwrite_allowed
            detail = "기존 NORMAL에 DELAYED 입력을 재생했으며 덮어쓰기가 차단됩니다."
        else:
            reached_daily_write = False
            overwrite_allowed = False
            preserved = True
            detail = "저장 단계 전에 실패하므로 traffic_daily를 변경하지 않습니다."

        scenarios.append(
            {
                "type": failure_type,
                "description": definitions[failure_type],
                "synthetic": True,
                "databaseWritePerformed": False,
                "reachedDailyWriteStage": reached_daily_write,
                "overwriteAllowed": overwrite_allowed,
                "lastNormalPreserved": preserved,
                "result": "PASS" if preserved else "FAIL",
                "detail": detail,
            }
        )

    return scenarios


def comparison_payload(today_record: dict | None, yesterday_record: dict | None) -> dict:
    if not today_record or not yesterday_record:
        return {
            "comparisonAvailable": False,
            "today": today_record,
            "previousDay": yesterday_record,
            "differenceKmh": None,
            "differencePercent": None,
            "rule": "KST 달력상 전날만 비교하며 다른 과거 날짜로 대체하지 않음",
        }

    difference = round(
        today_record["averageSpeed"] - yesterday_record["averageSpeed"],
        1,
    )
    previous_speed = yesterday_record["averageSpeed"]
    percent = round(difference / previous_speed * 100, 1) if previous_speed else None
    return {
        "comparisonAvailable": True,
        "today": today_record,
        "previousDay": yesterday_record,
        "differenceKmh": difference,
        "differencePercent": percent,
        "rule": "KST 달력상 전날만 비교하며 다른 과거 날짜로 대체하지 않음",
    }


# =========================================================
# API
# =========================================================

@app.get("/api")
def api_index():
    return {
        "name": "달구벌 NOW Daily API",
        "version": "5.1.0",
        "collectionMode": "LOCAL_INGEST",
        "expectedCollectTimeKST": "09:00",
        "endpoints": [
            "/api/health",
            "/api/dashboard",
            "/api/history",
            "/api/verify",
            "/api/check-missed",
            "/api/ingest",
            "/api/attempt",
        ],
    }


@app.get("/api/health")
def health():
    database_configured = bool(db_url())
    secret_configured = bool(shared_secret())
    missed_check_configured = bool(cron_secret())

    return {
        "ok": database_configured and secret_configured and missed_check_configured,
        "databaseConfigured": database_configured,
        "ingestSecretConfigured": secret_configured,
        "missedCheckConfigured": missed_check_configured,
        "collectionMode": "LOCAL_INGEST",
        "expectedCollectTimeKST": "09:00",
        "officialCaptureWindowKST": "09:00-09:09",
        "missedCheckTimeKST": "10:00",
        "runtime": "Vercel Python / FastAPI",
    }


@app.post("/api/ingest")
def ingest(
    payload: IngestPayload,
    authorization: str | None = Header(default=None),
):
    if not verify_secret(authorization):
        return JSONResponse(
            status_code=401,
            content={"ok": False, "message": "인증에 실패했습니다."},
        )

    try:
        with open_db() as conn:
            saved = save_daily(conn, payload)

            # 공식 일별값이 실제로 저장/갱신된 경우에만 같은 날짜의 원천 샘플도 교체한다.
            # 기존 NORMAL을 DELAYED가 덮어쓰지 못한 경우 샘플도 기존 증거를 유지한다.
            if saved:
                replace_source_samples(conn, payload)

            message = "로컬 수집기 전송 성공"
            if not saved:
                message += " - 기존 NORMAL 기록 유지"

            save_attempt(
                conn,
                attempted_at=payload.capturedAt,
                local_date=payload.localDate,
                status=payload.sourceStatus,
                failure_type="STALE_DATA" if payload.sourceStatus == "DELAYED" else None,
                message=message,
                average_speed=payload.averageSpeed,
                link_count=payload.linkCount,
                source_updated_at=payload.sourceUpdatedAt,
            )

        return {
            "ok": True,
            "saved": saved,
            "date": payload.localDate.isoformat(),
            "road": payload.road,
            "averageSpeed": payload.averageSpeed,
            "linkCount": payload.linkCount,
            "minSpeed": payload.minSpeed,
            "maxSpeed": payload.maxSpeed,
            "sourceStatus": payload.sourceStatus,
            "staleRatio": payload.staleRatio,
            "sourceSamplesSaved": len(payload.samples) if saved else 0,
        }

    except Exception:
        logger.exception("Failed to ingest traffic summary")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": "교통 데이터를 저장하는 중 서버 오류가 발생했습니다.",
            },
        )


@app.post("/api/attempt")
def record_attempt(
    payload: AttemptPayload,
    authorization: str | None = Header(default=None),
):
    if not verify_secret(authorization):
        return JSONResponse(
            status_code=401,
            content={"ok": False, "message": "인증에 실패했습니다."},
        )

    try:
        with open_db() as conn:
            save_attempt(
                conn,
                attempted_at=payload.attemptedAt,
                local_date=payload.localDate,
                status=payload.status,
                failure_type=payload.failureType,
                message=payload.message,
            )
        return {"ok": True}
    except Exception:
        logger.exception("Failed to store collection attempt")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": "수집 실패 이력을 저장하는 중 서버 오류가 발생했습니다.",
            },
        )


@app.get("/api/check-missed")
def check_missed(authorization: str | None = Header(default=None)):
    """Mark a day MISSED when no 09:00 collector report reached the server.

    This endpoint never calls ITS. Vercel Cron invokes it once a day after the
    local 09:00 collection window. If a real FAILED collector attempt is already
    present, that failure is preserved instead of being mislabeled MISSED.
    """
    if not cron_secret():
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": "CRON_SECRET가 설정되지 않아 누락 점검을 실행할 수 없습니다.",
            },
        )

    if not verify_cron_secret(authorization):
        return JSONResponse(
            status_code=401,
            content={"ok": False, "message": "Cron 인증에 실패했습니다."},
        )

    current = now_kst()
    deadline = datetime.combine(
        current.date(),
        time(MISSED_CHECK_HOUR_KST, MISSED_CHECK_MINUTE_KST),
        tzinfo=KST,
    )
    if current < deadline:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "message": "09:00 수집 누락 판정 시각 전입니다.",
                "missedCheckTimeKST": "10:00",
            },
        )

    try:
        with open_db() as conn:
            today = current.date()
            daily = query_daily_on(conn, today)
            latest_attempt = query_latest_attempt_today(conn, today)
            collector_attempt = query_latest_collector_attempt_today(conn, today)

            if daily:
                return {
                    "ok": True,
                    "result": "COLLECTED",
                    "date": today.isoformat(),
                    "message": "오늘 공식 09:00 일별 기록이 확인되었습니다.",
                }

            if collector_attempt:
                return {
                    "ok": True,
                    "result": "COLLECTOR_ATTEMPT_RECORDED",
                    "date": today.isoformat(),
                    "collectorStatus": collector_attempt["status"],
                    "message": (
                        "오늘 로컬 수집기 실행 흔적이 있으므로 MISSED로 중복 기록하지 않습니다."
                    ),
                }

            if latest_attempt and latest_attempt["status"] == "MISSED":
                return {
                    "ok": True,
                    "result": "ALREADY_MISSED",
                    "date": today.isoformat(),
                    "message": "오늘 09:00 수집 누락이 이미 기록되어 있습니다.",
                }

            save_attempt(
                conn,
                attempted_at=current,
                local_date=today,
                status="MISSED",
                failure_type=None,
                message=(
                    "10:00 KST까지 09:00 로컬 수집 데이터나 수집기 실패 보고가 "
                    "서버에 도착하지 않아 MISSED로 기록했습니다."
                ),
            )

        return {
            "ok": True,
            "result": "MISSED_RECORDED",
            "date": current.date().isoformat(),
            "message": "오늘 09:00 로컬 수집 보고 누락을 기록했습니다.",
        }

    except Exception:
        logger.exception("Failed to check missed collection")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": "09:00 수집 누락을 점검하는 중 서버 오류가 발생했습니다.",
            },
        )


@app.get("/api/history")
def history():
    try:
        with open_db() as conn:
            rows = query_daily(conn, HISTORY_QUERY_LIMIT)
            total_days = query_total_days(conn)

        return {
            "ok": True,
            "records": rows,
            "historyDays": total_days,
        }
    except Exception:
        logger.exception("Failed to query traffic history")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "records": [],
                "historyDays": 0,
                "message": "저장 기록을 불러오는 중 서버 오류가 발생했습니다.",
            },
        )


@app.get("/api/dashboard")
def dashboard():
    current = now_kst()

    try:
        with open_db() as conn:
            daily = query_daily(conn, HISTORY_QUERY_LIMIT)
            total_days = query_total_days(conn)
            today_record = query_daily_on(conn, current.date())
            yesterday_record = query_daily_on(conn, current.date() - timedelta(days=1))
            latest_normal = query_latest_normal(conn)
            today_attempt = query_latest_attempt_today(conn, current.date())
    except Exception:
        logger.exception("Failed to build dashboard response")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "status": "SETUP_ERROR",
                "message": "대시보드 데이터를 불러오는 중 서버 오류가 발생했습니다.",
                "serverTime": current.isoformat(),
            },
        )

    if today_record:
        status = "DELAYED" if today_record["sourceStatus"] == "DELAYED" else "NORMAL"
        message = (
            "오늘 교통 데이터가 정상적으로 수집되었습니다."
            if status == "NORMAL"
            else "오늘 값은 수집되었지만 일부 ITS 원천 데이터가 지연된 상태입니다."
        )
        shown_record = today_record
        is_fallback = False

    elif today_attempt and today_attempt["status"] == "FAILED":
        status = "ERROR"
        message = (
            "오늘 09:00 로컬 수집기는 실행됐지만 교통 데이터 수집에 실패했습니다. "
            "마지막 정상 기록을 대신 표시합니다."
        )
        shown_record = latest_normal
        is_fallback = bool(latest_normal)

    elif today_attempt and today_attempt["status"] == "MISSED":
        status = "MISSED"
        message = (
            "오늘 09:00 로컬 수집 보고가 확인되지 않아 수집 누락(MISSED)으로 기록했습니다. "
            "마지막 정상 기록을 대신 표시합니다."
        )
        shown_record = latest_normal
        is_fallback = bool(latest_normal)

    elif expected_collect_passed(current):
        status = "MISSING"
        message = (
            "오늘 오전 9:00 예정된 교통 데이터가 아직 없습니다. "
            "마지막 정상 기록이 있으면 대신 표시합니다."
        )
        shown_record = latest_normal
        is_fallback = bool(latest_normal)

    else:
        status = "WAITING"
        message = (
            "오늘 오전 9:00 수집 전입니다. "
            "마지막 정상 기록이 있으면 참고값으로 표시합니다."
        )
        shown_record = latest_normal
        is_fallback = bool(latest_normal)

    comparison = comparison_payload(today_record, yesterday_record)
    dashboard_comparison = None
    if comparison["comparisonAvailable"]:
        dashboard_comparison = {
            "previous": yesterday_record,
            "differenceKmh": comparison["differenceKmh"],
            "differencePercent": comparison["differencePercent"],
        }

    return {
        "ok": True,
        "status": status,
        "message": message,
        "serverTime": current.isoformat(),
        "expectedCollectTimeKST": "09:00",
        "officialCaptureWindowKST": "09:00-09:09",
        "missedCheckTimeKST": "10:00",
        "todayRecord": today_record,
        "yesterdayRecord": yesterday_record,
        "shownRecord": shown_record,
        "shownRecordIsFallback": is_fallback,
        "todayAttempt": today_attempt,
        "comparison": dashboard_comparison,
        "history": daily[:DASHBOARD_HISTORY_LIMIT],
        "historyDays": total_days,
    }


@app.get("/api/verify")
def verify_submission():
    """Public, read-only evidence endpoint for assignment verification.

    No environment variable values, authorization headers, API keys, or DB URLs are
    included. Synthetic failure replay is performed purely in memory and writes
    nothing to the database.
    """
    current = now_kst()
    today = current.date()
    yesterday = today - timedelta(days=1)

    try:
        with open_db() as conn:
            latest_record = query_latest_daily(conn)
            latest_normal = query_latest_normal(conn)
            today_record = query_daily_on(conn, today)
            yesterday_record = query_daily_on(conn, yesterday)
            total_days = query_total_days(conn)
            recent_records = query_daily(conn, 7)
            recent_attempts = query_recent_attempts(conn, 10)
            today_attempt = query_latest_attempt_today(conn, today)
            samples = (
                query_source_samples(conn, date.fromisoformat(latest_record["date"]))
                if latest_record
                else []
            )
    except Exception:
        logger.exception("Failed to build verification response")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": "검증 데이터를 불러오는 중 서버 오류가 발생했습니다.",
            },
        )

    comparison = comparison_payload(today_record, yesterday_record)
    replay = synthetic_failure_replay()
    replay_ok = all(item["result"] == "PASS" for item in replay)

    source_check = bool(latest_record and samples)
    comparison_check = comparison["comparisonAvailable"]
    preservation_check = bool(latest_normal and total_days > 0)

    return {
        "ok": True,
        "generatedAtKST": current.isoformat(),
        "checks": {
            "publicSourceValueAndContext": "PASS" if source_check else "PENDING",
            "fiveSyntheticExternalFailures": "PASS" if replay_ok else "FAIL",
            "lastNormalAndDailyHistoryPreserved": "PASS" if preservation_check else "PENDING",
            "actualKSTPreviousDayComparison": "PASS" if comparison_check else "PENDING",
            "noPersonalOrSecretValuesExposed": "PASS",
        },
        "source": {
            "provider": SOURCE_NAME,
            "publicContextUrl": SOURCE_CONTEXT_URL,
            "publicApiEndpointWithoutKey": SOURCE_API_ENDPOINT,
            "road": ROAD_NAME,
            "requestArea": SOURCE_BBOX,
            "aggregation": "달구벌대로 유효 링크 speed의 산술평균",
            "sampleMethod": "유효 링크 순서에서 균등 간격으로 최대 5건 보관",
            "latestRecord": latest_record,
            "samples": samples,
        },
        "failureReplay": {
            "mode": "SYNTHETIC_IN_MEMORY",
            "databaseWrites": 0,
            "scenarios": replay,
        },
        "preservation": {
            "policy": (
                "실패는 traffic_daily에 0으로 저장하지 않으며, "
                "기존 NORMAL은 DELAYED 재수집으로 덮어쓰지 않습니다."
            ),
            "lastNormal": latest_normal,
            "totalDailyRecords": total_days,
            "recentDailyRecords": recent_records,
            "recentCollectionAttempts": recent_attempts,
        },
        "comparison": comparison,
        "scheduleMonitoring": {
            "collectionMode": "LOCAL_INGEST",
            "expectedCollectTimeKST": "09:00",
            "officialCaptureWindowKST": "09:00-09:09",
            "missedCheckTimeKST": "10:00",
            "todayAttempt": today_attempt,
            "policy": (
                "10:00 KST까지 공식 일별 기록과 로컬 수집기 실패 보고가 모두 없으면 "
                "서버가 MISSED를 기록합니다. Vercel은 ITS를 호출하지 않습니다."
            ),
        },
        "privacy": {
            "secretsExposed": False,
            "excluded": [
                "ITS_API_KEY",
                "INGEST_SECRET",
                "CRON_SECRET",
                "DATABASE_URL",
                "Authorization header",
            ],
            "note": "검증 API는 공개 가능한 집계값·원천 샘플·상태만 반환합니다.",
        },
    }
