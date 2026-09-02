import hmac
import json
import os
import ssl
import statistics
from datetime import datetime, time
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen

import psycopg
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="달구벌 NOW API", version="3.0")

KST = ZoneInfo("Asia/Seoul")
ITS_API_URL = os.environ.get(
    "ITS_API_URL",
    "https://openapi.its.go.kr:9443/trafficInfo",
).strip()

ROAD_KEYWORD = "달구벌대로"

BBOX = {
    "minX": 128.40,
    "maxX": 128.80,
    "minY": 35.75,
    "maxY": 36.00,
}

EXPECTED_HOUR_KST = 9
EXPECTED_MINUTE_KST = 30


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def db_url() -> str:
    return env("DATABASE_URL") or env("POSTGRES_URL")


def now_kst() -> datetime:
    return datetime.now(KST)


def ensure_tables(conn):
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        """
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
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attempts_date_time
        ON collection_attempts(local_date, attempted_at DESC)
        """
    )


def open_db():
    url = db_url()
    if not url:
        raise RuntimeError(
            "DATABASE_URL이 설정되지 않았습니다. "
            "Vercel 프로젝트에 Neon Postgres를 연결하세요."
        )
    return psycopg.connect(url, autocommit=True)


def parse_its_datetime(value):
    if not value:
        return None

    digits = "".join(ch for ch in str(value) if ch.isdigit())

    for fmt, length in (
        ("%Y%m%d%H%M%S", 14),
        ("%Y%m%d%H%M", 12),
        ("%Y%m%d%H", 10),
    ):
        if len(digits) >= length:
            try:
                return datetime.strptime(
                    digits[:length], fmt
                ).replace(tzinfo=KST)
            except ValueError:
                pass

    return None


def get_value(item, *names):
    lower = {str(key).lower(): value for key, value in item.items()}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def find_traffic_items(obj):
    found = []

    if isinstance(obj, dict):
        keys = {str(key).lower() for key in obj.keys()}
        has_road = "roadname" in keys or "road_name" in keys
        has_speed = "speed" in keys

        if has_road and has_speed:
            found.append(obj)

        for value in obj.values():
            found.extend(find_traffic_items(value))

    elif isinstance(obj, list):
        for value in obj:
            found.extend(find_traffic_items(value))

    return found


def detect_api_error(data):
    stack = [data]

    while stack:
        current = stack.pop()

        if isinstance(current, dict):
            lower = {str(key).lower(): value for key, value in current.items()}

            if "resultcode" in lower:
                code = str(lower.get("resultcode", "")).strip()
                message = str(lower.get("resultmsg", "")).strip()

                if code not in ("", "0", "00", "000", "SUCCESS", "success"):
                    return f"ITS 응답 오류 {code}: {message or '실패'}"

            stack.extend(current.values())

        elif isinstance(current, list):
            stack.extend(current)

    return None

def save_successful_daily(conn, data):
    ensure_tables(conn)

    conn.execute(
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
        ON CONFLICT (local_date) DO UPDATE SET
            captured_at = EXCLUDED.captured_at,
            road_name = EXCLUDED.road_name,
            average_speed = EXCLUDED.average_speed,
            link_count = EXCLUDED.link_count,
            min_speed = EXCLUDED.min_speed,
            max_speed = EXCLUDED.max_speed,
            source_updated_at = EXCLUDED.source_updated_at,
            source_status = EXCLUDED.source_status,
            updated_at = NOW()
        """,
        (
            data["localDate"],
            data["capturedAt"],
            data["road"],
            data["averageSpeed"],
            data["linkCount"],
            data["minSpeed"],
            data["maxSpeed"],
            data["sourceUpdatedAt"],
            data["sourceStatus"],
        ),
    )


def save_attempt(
    conn,
    status: str,
    message: str | None = None,
    data: dict | None = None,
):
    ensure_tables(conn)
    attempted_at = now_kst()

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
            attempted_at.date(),
            status,
            message,
            data.get("averageSpeed") if data else None,
            data.get("linkCount") if data else None,
            data.get("sourceUpdatedAt") if data else None,
        ),
    )


def serialize_daily(row):
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


def query_daily(conn, limit=14):
    ensure_tables(conn)

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


def query_latest_attempt_today(conn, today):
    ensure_tables(conn)

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
        "linkCount": row[4],
        "sourceUpdatedAt": row[5].isoformat() if row[5] else None,
    }


def expected_collect_passed(current):
    expected = datetime.combine(
        current.date(),
        time(EXPECTED_HOUR_KST, EXPECTED_MINUTE_KST),
        tzinfo=KST,
    )
    return current >= expected

@app.get("/api")
def api_index():
    return {
        "name": "달구벌 NOW Daily API",
        "version": "3.0",
        "endpoints": [
            "/api/health",
            "/api/dashboard",
            "/api/history",
        ],
    }


@app.get("/api/health")
def health():
    return {
        "ok": bool(
            db_url()
            and env("INGEST_SECRET")
        ),
        "databaseConfigured": bool(db_url()),
        "ingestSecretConfigured": bool(env("INGEST_SECRET")),
        "runtime": "Vercel Python / FastAPI",
    }

@app.get("/api/history")
def history():
    try:
        with open_db() as conn:
            rows = query_daily(conn, 30)

        return {
            "ok": True,
            "records": rows,
            "historyDays": len(rows),
        }

    except Exception as exc:
        return {
            "ok": False,
            "records": [],
            "historyDays": 0,
            "message": str(exc),
        }


@app.get("/api/dashboard")
def dashboard():
    current = now_kst()

    try:
        with open_db() as conn:
            daily = query_daily(conn, 14)
            today_attempt = query_latest_attempt_today(
                conn,
                current.date(),
            )

    except Exception as exc:
        return {
            "ok": False,
            "status": "SETUP_ERROR",
            "message": str(exc),
            "serverTime": current.isoformat(),
        }

    today_record = next(
        (
            row for row in daily
            if row["date"] == current.date().isoformat()
        ),
        None,
    )

    latest_normal = daily[0] if daily else None

    previous_record = None
    if today_record:
        previous_record = next(
            (
                row for row in daily
                if row["date"] < today_record["date"]
            ),
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
            else (
                "오늘 값은 수집되었지만 ITS 원천 데이터의 "
                "생성시각이 오래된 상태입니다."
            )
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
            "오늘 예정된 자동 수집 기록이 아직 없습니다. "
            "Cron 실행 상태를 확인하세요. 마지막 정상 기록이 있으면 대신 표시합니다."
        )
        shown_record = latest_normal
        is_fallback = bool(latest_normal)

    else:
        status = "WAITING"
        message = (
            "오늘 오전 9:30 자동 수집 전입니다. "
            "마지막 정상 기록이 있으면 참고값으로 표시합니다."
        )
        shown_record = latest_normal
        is_fallback = bool(latest_normal)

    comparison = None

    if today_record and previous_record:
        difference = round(
            today_record["averageSpeed"]
            - previous_record["averageSpeed"],
            1,
        )

        percent = (
            round(
                difference
                / previous_record["averageSpeed"]
                * 100,
                1,
            )
            if previous_record["averageSpeed"]
            else 0
        )

        comparison = {
            "previous": previous_record,
            "differenceKmh": difference,
            "differencePercent": percent,
        }

    return {
        "ok": True,
        "status": status,
        "message": message,
        "serverTime": current.isoformat(),
        "expectedCollectTimeKST": "09:30",
        "todayRecord": today_record,
        "shownRecord": shown_record,
        "shownRecordIsFallback": is_fallback,
        "todayAttempt": today_attempt,
        "comparison": comparison,
        "history": daily[:7],
        "historyDays": len(daily),
    }

@app.post("/api/ingest")
async def ingest(
    request: Request,
    authorization: str | None = Header(default=None),
):
    secret = env("INGEST_SECRET")

    if not secret:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "message": "INGEST_SECRET가 설정되지 않았습니다."
            },
        )

    expected = f"Bearer {secret}"

    if not hmac.compare_digest(authorization or "", expected):
        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "message": "Ingest 인증에 실패했습니다."
            },
        )

    try:
        body = await request.json()

        captured_at = datetime.fromisoformat(body["capturedAt"])
        local_date = captured_at.astimezone(KST).date()

        source_updated_at = None
        if body.get("sourceUpdatedAt"):
            source_updated_at = datetime.fromisoformat(
                body["sourceUpdatedAt"]
            )

        data = {
            "localDate": local_date,
            "capturedAt": captured_at,
            "road": "달구벌대로",
            "averageSpeed": float(body["averageSpeed"]),
            "linkCount": int(body["linkCount"]),
            "minSpeed": float(body["minSpeed"]),
            "maxSpeed": float(body["maxSpeed"]),
            "sourceUpdatedAt": source_updated_at,
            "sourceStatus": body.get("sourceStatus", "NORMAL"),
        }

        with open_db() as conn:
            save_successful_daily(conn, data)
            save_attempt(
                conn,
                status=data["sourceStatus"],
                message="GitHub Actions 자동 수집",
                data=data,
            )

        return {
            "ok": True,
            "date": local_date.isoformat(),
            "averageSpeed": data["averageSpeed"],
        }

    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "message": str(exc),
            },
        )
