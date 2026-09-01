import hmac
import json
import os
import ssl
import statistics
from datetime import datetime, time
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg
from fastapi import FastAPI, Header
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


def fetch_dalgubeol_traffic():
    api_key = env("ITS_API_KEY")

    if not api_key:
        raise RuntimeError(
            "ITS_API_KEY가 설정되지 않았습니다. "
            "ITS 인증키 승인 후 Vercel 환경변수에 등록하세요."
        )

    params = {
        "apiKey": api_key,
        "type": "all",
        "drcType": "all",
        "minX": BBOX["minX"],
        "maxX": BBOX["maxX"],
        "minY": BBOX["minY"],
        "maxY": BBOX["maxY"],
        "getType": "json",
    }

    request_url = ITS_API_URL + "?" + urlencode(params)

    request = Request(
        request_url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Dalgubeol-NOW-Daily/3.0",
        },
        method="GET",
    )

    try:
        with urlopen(
            request,
            timeout=20,
            context=ssl.create_default_context(),
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise RuntimeError(f"ITS API HTTP 오류: {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"ITS API 연결 오류: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("ITS API 응답 시간이 초과되었습니다.") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = raw[:180].replace("\n", " ")
        raise RuntimeError(
            f"ITS API가 JSON을 반환하지 않았습니다: {preview}"
        ) from exc

    api_error = detect_api_error(data)
    if api_error:
        raise RuntimeError(api_error)

    all_items = find_traffic_items(data)
    road_items = []

    for item in all_items:
        road_name = str(
            get_value(item, "roadName", "road_name") or ""
        ).strip()

        if ROAD_KEYWORD not in road_name:
            continue

        try:
            speed = float(get_value(item, "speed"))
        except (TypeError, ValueError):
            continue

        if speed <= 0 or speed > 200:
            continue

        road_items.append(
            {
                "roadName": road_name,
                "linkId": str(
                    get_value(item, "linkId", "link_id") or ""
                ),
                "startNodeId": str(
                    get_value(item, "startNodeId", "start_node_id") or ""
                ),
                "endNodeId": str(
                    get_value(item, "endNodeId", "end_node_id") or ""
                ),
                "speed": round(speed, 1),
                "travelTime": get_value(
                    item, "travelTime", "travel_time"
                ),
                "createdDate": str(
                    get_value(item, "createdDate", "created_date") or ""
                ),
            }
        )

    if not road_items:
        raise RuntimeError(
            "대구 조회 영역에서 roadName에 '달구벌대로'가 포함된 "
            "유효 ITS 교통 링크를 찾지 못했습니다."
        )

    speeds = [item["speed"] for item in road_items]

    source_times = [
        parse_its_datetime(item["createdDate"])
        for item in road_items
        if item.get("createdDate")
    ]
    source_times = [dt for dt in source_times if dt]

    latest_source = max(source_times) if source_times else None
    captured_at = now_kst()

    source_age_minutes = None
    source_status = "NORMAL"

    if latest_source:
        source_age_minutes = max(
            0,
            round(
                (captured_at - latest_source).total_seconds() / 60,
                1,
            ),
        )

        if source_age_minutes >= 30:
            source_status = "DELAYED"

    return {
        "road": ROAD_KEYWORD,
        "capturedAt": captured_at,
        "localDate": captured_at.date(),
        "averageSpeed": round(statistics.fmean(speeds), 1),
        "linkCount": len(road_items),
        "minSpeed": round(min(speeds), 1),
        "maxSpeed": round(max(speeds), 1),
        "sourceUpdatedAt": latest_source,
        "sourceStatus": source_status,
        "sourceAgeMinutes": source_age_minutes,
        "slowLinks": sorted(
            road_items,
            key=lambda item: item["speed"],
        )[:10],
    }


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


def verify_cron_secret(authorization: str | None):
    secret = env("CRON_SECRET")

    if not secret:
        return False, (
            "CRON_SECRET가 설정되지 않았습니다. "
            "Vercel 환경변수에 긴 임의 문자열을 등록하세요."
        )

    expected = f"Bearer {secret}"
    actual = authorization or ""

    if not hmac.compare_digest(actual, expected):
        return False, "Cron 인증에 실패했습니다."

    return True, None


@app.get("/api")
def api_index():
    return {
        "name": "달구벌 NOW Daily API",
        "version": "3.0",
        "endpoints": [
            "/api/health",
            "/api/dashboard",
            "/api/history",
            "/api/collect",
        ],
    }


@app.get("/api/health")
def health():
    return {
        "ok": bool(
            env("ITS_API_KEY")
            and db_url()
            and env("CRON_SECRET")
        ),
        "itsApiKeyConfigured": bool(env("ITS_API_KEY")),
        "databaseConfigured": bool(db_url()),
        "cronSecretConfigured": bool(env("CRON_SECRET")),
        "expectedCollectTimeKST": "09:30",
        "runtime": "Vercel Python / FastAPI",
    }


@app.get("/api/collect")
def collect(authorization: str | None = Header(default=None)):
    valid, auth_error = verify_cron_secret(authorization)

    if not valid:
        return JSONResponse(
            status_code=401,
            content={
                "ok": False,
                "status": "UNAUTHORIZED",
                "message": auth_error,
            },
        )

    try:
        conn = open_db()
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "status": "DB_ERROR",
                "message": str(exc),
            },
        )

    with conn:
        ensure_tables(conn)

        try:
            data = fetch_dalgubeol_traffic()
            save_successful_daily(conn, data)

            attempt_status = (
                "DELAYED"
                if data["sourceStatus"] == "DELAYED"
                else "SUCCESS"
            )

            attempt_message = (
                "ITS 원천 데이터 생성시각이 오래되었습니다."
                if attempt_status == "DELAYED"
                else "정상 수집"
            )

            save_attempt(
                conn,
                status=attempt_status,
                message=attempt_message,
                data=data,
            )

            return {
                "ok": True,
                "status": attempt_status,
                "date": data["localDate"].isoformat(),
                "capturedAt": data["capturedAt"].isoformat(),
                "averageSpeed": data["averageSpeed"],
                "linkCount": data["linkCount"],
                "sourceUpdatedAt": (
                    data["sourceUpdatedAt"].isoformat()
                    if data["sourceUpdatedAt"]
                    else None
                ),
                "sourceAgeMinutes": data["sourceAgeMinutes"],
                "slowLinks": data["slowLinks"],
            }

        except Exception as exc:
            save_attempt(
                conn,
                status="FAILED",
                message=str(exc),
            )

            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "status": "FAILED",
                    "message": str(exc),
                },
            )


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
