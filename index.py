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

app = FastAPI(title="달구벌 NOW API", version="4.1.0")

KST = ZoneInfo("Asia/Seoul")
ROAD_NAME = "달구벌대로"
EXPECTED_HOUR_KST = 9
EXPECTED_MINUTE_KST = 0
HISTORY_QUERY_LIMIT = 30
DASHBOARD_HISTORY_LIMIT = 7


# =========================================================
# 환경 / 시간
# =========================================================

def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def db_url() -> str:
    return env("DATABASE_URL") or env("POSTGRES_URL")


def shared_secret() -> str:
    # 새 이름을 권장하되 기존 배포의 CRON_SECRET도 호환한다.
    return env("INGEST_SECRET") or env("CRON_SECRET")


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


# =========================================================
# 요청 모델
# =========================================================

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

    # 수집기 진단용. DB에는 저장하지 않지만 입력값 검증/응답에 활용한다.
    sourceTimestampCount: int | None = Field(default=None, ge=0, le=10000)
    staleLinkCount: int | None = Field(default=None, ge=0, le=10000)
    staleRatio: float | None = Field(default=None, ge=0, le=1)

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
    return psycopg.connect(url, autocommit=True)


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


def query_daily(conn, limit: int = HISTORY_QUERY_LIMIT) -> list[dict]:
    rows = conn.execute(
        """
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
        ORDER BY local_date DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [serialize_daily(row) for row in rows]


def query_total_days(conn) -> int:
    row = conn.execute("SELECT COUNT(*) FROM traffic_daily").fetchone()
    return int(row[0]) if row else 0


def query_latest_attempt_today(conn, today: date) -> dict | None:
    row = conn.execute(
        """
        SELECT
            attempted_at,
            status,
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
        "message": row[2],
        "averageSpeed": float(row[3]) if row[3] is not None else None,
        "linkCount": int(row[4]) if row[4] is not None else None,
        "sourceUpdatedAt": row[5].isoformat() if row[5] else None,
    }


def save_attempt(
    conn,
    *,
    attempted_at: datetime,
    local_date: date,
    status: str,
    message: str | None,
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
            message,
            average_speed,
            link_count,
            source_updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            attempted_at,
            local_date,
            status,
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


# =========================================================
# API
# =========================================================

@app.get("/api")
def api_index():
    return {
        "name": "달구벌 NOW Daily API",
        "version": "4.1.0",
        "collectionMode": "LOCAL_INGEST",
        "expectedCollectTimeKST": "09:00",
        "endpoints": [
            "/api/health",
            "/api/dashboard",
            "/api/history",
            "/api/ingest",
            "/api/attempt",
        ],
    }


@app.get("/api/health")
def health():
    database_configured = bool(db_url())
    secret_configured = bool(shared_secret())

    return {
        "ok": database_configured and secret_configured,
        "databaseConfigured": database_configured,
        "ingestSecretConfigured": secret_configured,
        "collectionMode": "LOCAL_INGEST",
        "expectedCollectTimeKST": "09:00",
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

            message = "로컬 수집기 전송 성공"
            if not saved:
                message += " - 기존 NORMAL 기록 유지"

            save_attempt(
                conn,
                attempted_at=payload.capturedAt,
                local_date=payload.localDate,
                status=payload.sourceStatus,
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

    today_iso = current.date().isoformat()
    yesterday_iso = (current.date() - timedelta(days=1)).isoformat()

    today_record = next((row for row in daily if row["date"] == today_iso), None)
    yesterday_record = next(
        (row for row in daily if row["date"] == yesterday_iso),
        None,
    )
    latest_normal = next(
        (row for row in daily if row["sourceStatus"] == "NORMAL"),
        None,
    )

    if today_record:
        status = (
            "DELAYED"
            if today_record["sourceStatus"] == "DELAYED"
            else "NORMAL"
        )
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
            "오늘 교통 데이터 수집에 실패했습니다. "
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

    comparison = None
    if today_record and yesterday_record:
        difference = round(
            today_record["averageSpeed"] - yesterday_record["averageSpeed"],
            1,
        )
        previous_speed = yesterday_record["averageSpeed"]
        percent = round(difference / previous_speed * 100, 1) if previous_speed else 0
        comparison = {
            "previous": yesterday_record,
            "differenceKmh": difference,
            "differencePercent": percent,
        }

    return {
        "ok": True,
        "status": status,
        "message": message,
        "serverTime": current.isoformat(),
        "expectedCollectTimeKST": "09:00",
        "todayRecord": today_record,
        "yesterdayRecord": yesterday_record,
        "shownRecord": shown_record,
        "shownRecordIsFallback": is_fallback,
        "todayAttempt": today_attempt,
        "comparison": comparison,
        "history": daily[:DASHBOARD_HISTORY_LIMIT],
        "historyDays": total_days,
    }
