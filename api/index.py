import hmac
import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse


app = FastAPI(
    title="달구벌 NOW API",
    version="3.0",
)


# =========================
# 기본 설정
# =========================

KST = ZoneInfo("Asia/Seoul")

ROAD_NAME = "달구벌대로"

EXPECTED_HOUR_KST = 9
EXPECTED_MINUTE_KST = 30


# =========================
# 환경변수 / 시간
# =========================

def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def db_url() -> str:
    return (
        env("DATABASE_URL")
        or env("POSTGRES_URL")
    )


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_iso_datetime(
    value,
    field_name: str,
) -> datetime:

    if not value:
        raise ValueError(
            f"{field_name} 값이 없습니다."
        )

    text = str(value).strip()

    # ISO 8601의 Z 표기 지원
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)

    except ValueError as exc:
        raise ValueError(
            f"{field_name} 날짜 형식이 올바르지 않습니다."
        ) from exc

    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name}에는 시간대 정보가 필요합니다."
        )

    return parsed


# =========================
# DB
# =========================

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
        ON collection_attempts(
            local_date,
            attempted_at DESC
        )
        """
    )


def open_db():

    url = db_url()

    if not url:
        raise RuntimeError(
            "DATABASE_URL이 설정되지 않았습니다. "
            "Vercel 프로젝트에 Neon Postgres를 연결하세요."
        )

    return psycopg.connect(
        url,
        autocommit=True,
    )


# =========================
# 일일 교통 데이터 저장
# =========================

def save_successful_daily(
    conn,
    data,
):

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
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            NOW()
        )

        ON CONFLICT (local_date)

        DO UPDATE SET
            captured_at =
                EXCLUDED.captured_at,

            road_name =
                EXCLUDED.road_name,

            average_speed =
                EXCLUDED.average_speed,

            link_count =
                EXCLUDED.link_count,

            min_speed =
                EXCLUDED.min_speed,

            max_speed =
                EXCLUDED.max_speed,

            source_updated_at =
                EXCLUDED.source_updated_at,

            source_status =
                EXCLUDED.source_status,

            updated_at =
                NOW()
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


# =========================
# 수집 시도 기록
# =========================

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
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
        """,
        (
            attempted_at,
            attempted_at.date(),
            status,
            message,

            (
                data.get("averageSpeed")
                if data
                else None
            ),

            (
                data.get("linkCount")
                if data
                else None
            ),

            (
                data.get("sourceUpdatedAt")
                if data
                else None
            ),
        ),
    )


# =========================
# DB 데이터 직렬화
# =========================

def serialize_daily(row):

    if not row:
        return None

    return {
        "date":
            row[0].isoformat(),

        "capturedAt":
            row[1].isoformat(),

        "roadName":
            row[2],

        "averageSpeed":
            float(row[3]),

        "linkCount":
            int(row[4]),

        "minSpeed":
            (
                float(row[5])
                if row[5] is not None
                else None
            ),

        "maxSpeed":
            (
                float(row[6])
                if row[6] is not None
                else None
            ),

        "sourceUpdatedAt":
            (
                row[7].isoformat()
                if row[7]
                else None
            ),

        "sourceStatus":
            row[8],
    }


# =========================
# 일별 데이터 조회
# =========================

def query_daily(
    conn,
    limit=30,
):

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

    return [
        serialize_daily(row)
        for row in rows
    ]


# =========================
# 오늘 마지막 수집 시도
# =========================

def query_latest_attempt_today(
    conn,
    today,
):

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
        "attemptedAt":
            row[0].isoformat(),

        "status":
            row[1],

        "message":
            row[2],

        "averageSpeed":
            (
                float(row[3])
                if row[3] is not None
                else None
            ),

        "linkCount":
            row[4],

        "sourceUpdatedAt":
            (
                row[5].isoformat()
                if row[5]
                else None
            ),
    }


# =========================
# 예정 수집 시간 확인
# =========================

def expected_collect_passed(
    current,
):

    expected = datetime.combine(
        current.date(),
        time(
            EXPECTED_HOUR_KST,
            EXPECTED_MINUTE_KST,
        ),
        tzinfo=KST,
    )

    return current >= expected


# =========================
# API 기본 정보
# =========================

@app.get("/api")
def api_index():

    return {
        "name":
            "달구벌 NOW Daily API",

        "version":
            "3.0",

        "endpoints": [
            "/api/health",
            "/api/dashboard",
            "/api/history",
            "/api/ingest",
        ],
    }


# =========================
# Health Check
# =========================

@app.get("/api/health")
def health():

    database_configured = bool(
        db_url()
    )

    ingest_configured = bool(
        env("INGEST_SECRET")
    )

    return {
        "ok":
            (
                database_configured
                and ingest_configured
            ),

        "databaseConfigured":
            database_configured,

        "ingestSecretConfigured":
            ingest_configured,

        "runtime":
            "Vercel Python / FastAPI",
    }


# =========================
# 과거 기록 조회
# =========================

@app.get("/api/history")
def history():

    try:

        with open_db() as conn:

            rows = query_daily(
                conn,
                30,
            )

        return {
            "ok":
                True,

            "records":
                rows,

            "historyDays":
                len(rows),
        }

    except Exception as exc:

        return {
            "ok":
                False,

            "records":
                [],

            "historyDays":
                0,

            "message":
                str(exc),
        }


# =========================
# 대시보드
# =========================

@app.get("/api/dashboard")
def dashboard():

    current = now_kst()

    try:

        with open_db() as conn:

            daily = query_daily(
                conn,
                30,
            )

            today_attempt = (
                query_latest_attempt_today(
                    conn,
                    current.date(),
                )
            )

    except Exception as exc:

        return {
            "ok":
                False,

            "status":
                "SETUP_ERROR",

            "message":
                str(exc),

            "serverTime":
                current.isoformat(),
        }


    # -------------------------
    # 오늘 기록
    # -------------------------

    today_record = next(
        (
            row
            for row in daily

            if (
                row["date"]
                == current.date().isoformat()
            )
        ),
        None,
    )


    # -------------------------
    # 마지막 정상 데이터
    # -------------------------

    latest_normal = next(
        (
            row
            for row in daily

            if (
                row["sourceStatus"]
                == "NORMAL"
            )
        ),
        None,
    )


    # -------------------------
    # 직전 실제 날짜 기록
    # -------------------------

    previous_record = None

    if today_record:

        previous_record = next(
            (
                row
                for row in daily

                if (
                    row["date"]
                    < today_record["date"]
                )
            ),
            None,
        )


    # -------------------------
    # 오늘 데이터 존재
    # -------------------------

    if today_record:

        status = (
            "DELAYED"
            if (
                today_record["sourceStatus"]
                == "DELAYED"
            )
            else "NORMAL"
        )

        if status == "NORMAL":

            message = (
                "오늘 교통 데이터가 "
                "정상적으로 수집되었습니다."
            )

        else:

            message = (
                "오늘 값은 수집되었지만 "
                "ITS 원천 데이터의 생성시각이 "
                "오래된 상태입니다."
            )

        shown_record = today_record

        is_fallback = False


    # -------------------------
    # 명시적 실패 기록 존재
    # -------------------------

    elif (
        today_attempt
        and today_attempt["status"]
        == "FAILED"
    ):

        status = "ERROR"

        message = (
            "오늘 교통 데이터 수집에 "
            "실패했습니다. "
            "마지막 정상 기록을 "
            "대신 표시합니다."
        )

        shown_record = latest_normal

        is_fallback = bool(
            latest_normal
        )


    # -------------------------
    # 수집 예정 시간 지났는데 없음
    # -------------------------

    elif expected_collect_passed(
        current
    ):

        status = "MISSING"

        message = (
            "오늘 예정된 교통 데이터가 "
            "아직 수집되지 않았습니다. "
            "외부 수집기 실행 상태를 "
            "확인하세요. "
            "마지막 정상 기록이 있으면 "
            "대신 표시합니다."
        )

        shown_record = latest_normal

        is_fallback = bool(
            latest_normal
        )


    # -------------------------
    # 아직 수집 시간 전
    # -------------------------

    else:

        status = "WAITING"

        message = (
            "오늘 오전 9:30 "
            "자동 수집 전입니다. "
            "마지막 정상 기록이 있으면 "
            "참고값으로 표시합니다."
        )

        shown_record = latest_normal

        is_fallback = bool(
            latest_normal
        )


    # =========================
    # 전일 대비 비교
    # =========================

    comparison = None

    if (
        today_record
        and previous_record
    ):

        difference = round(
            (
                today_record[
                    "averageSpeed"
                ]
                -
                previous_record[
                    "averageSpeed"
                ]
            ),
            1,
        )

        if previous_record[
            "averageSpeed"
        ]:

            percent = round(
                (
                    difference
                    /
                    previous_record[
                        "averageSpeed"
                    ]
                    * 100
                ),
                1,
            )

        else:

            percent = 0


        comparison = {
            "previous":
                previous_record,

            "differenceKmh":
                difference,

            "differencePercent":
                percent,
        }


    # =========================
    # 최종 응답
    # =========================

    return {
        "ok":
            True,

        "status":
            status,

        "message":
            message,

        "serverTime":
            current.isoformat(),

        "expectedCollectTimeKST":
            "09:30",

        "todayRecord":
            today_record,

        "shownRecord":
            shown_record,

        "shownRecordIsFallback":
            is_fallback,

        "todayAttempt":
            today_attempt,

        "comparison":
            comparison,

        "history":
            daily[:7],

        "historyDays":
            len(daily),
    }


# =========================
# 외부 수집기 → Vercel
# =========================

@app.post("/api/ingest")
async def ingest(
    request: Request,
    authorization: str | None = Header(
        default=None
    ),
):

    # -------------------------
    # 인증키 확인
    # -------------------------

    secret = env(
        "INGEST_SECRET"
    )

    if not secret:

        return JSONResponse(
            status_code=500,
            content={
                "ok":
                    False,

                "message":
                    (
                        "INGEST_SECRET가 "
                        "설정되지 않았습니다."
                    ),
            },
        )


    expected = (
        f"Bearer {secret}"
    )


    if not hmac.compare_digest(
        authorization or "",
        expected,
    ):

        return JSONResponse(
            status_code=401,
            content={
                "ok":
                    False,

                "message":
                    "Ingest 인증에 실패했습니다.",
            },
        )


    # -------------------------
    # 데이터 처리
    # -------------------------

    try:

        body = await request.json()


        required_fields = [
            "capturedAt",
            "averageSpeed",
            "linkCount",
            "minSpeed",
            "maxSpeed",
        ]


        missing_fields = [
            field
            for field in required_fields
            if field not in body
        ]


        if missing_fields:

            raise ValueError(
                "필수 값이 없습니다: "
                + ", ".join(
                    missing_fields
                )
            )


        captured_at = (
            parse_iso_datetime(
                body["capturedAt"],
                "capturedAt",
            )
        )


        local_date = (
            captured_at
            .astimezone(KST)
            .date()
        )


        source_updated_at = None


        if body.get(
            "sourceUpdatedAt"
        ):

            source_updated_at = (
                parse_iso_datetime(
                    body[
                        "sourceUpdatedAt"
                    ],
                    "sourceUpdatedAt",
                )
            )


        # -------------------------
        # 값 검증
        # -------------------------

        average_speed = float(
            body["averageSpeed"]
        )

        link_count = int(
            body["linkCount"]
        )

        min_speed = float(
            body["minSpeed"]
        )

        max_speed = float(
            body["maxSpeed"]
        )


        if link_count <= 0:

            raise ValueError(
                "linkCount는 "
                "1 이상이어야 합니다."
            )


        if average_speed <= 0:

            raise ValueError(
                "averageSpeed는 "
                "0보다 커야 합니다."
            )


        if min_speed <= 0:

            raise ValueError(
                "minSpeed는 "
                "0보다 커야 합니다."
            )


        if max_speed <= 0:

            raise ValueError(
                "maxSpeed는 "
                "0보다 커야 합니다."
            )


        if min_speed > max_speed:

            raise ValueError(
                "minSpeed가 "
                "maxSpeed보다 클 수 없습니다."
            )


        # -------------------------
        # 원천 데이터 상태 확인
        # -------------------------

        source_status = str(
            body.get(
                "sourceStatus",
                "NORMAL",
            )
        ).upper()


        if source_status not in (
            "NORMAL",
            "DELAYED",
        ):

            source_status = "NORMAL"


        # 원천 데이터 생성시각이
        # 30분 이상 오래되면
        # Vercel에서도 DELAYED 처리
        if source_updated_at:

            source_age_minutes = (
                (
                    captured_at.astimezone(KST)
                    -
                    source_updated_at.astimezone(KST)
                )
                .total_seconds()
                / 60
            )


            if (
                source_age_minutes
                >= 30
            ):

                source_status = (
                    "DELAYED"
                )


        data = {
            "localDate":
                local_date,

            "capturedAt":
                captured_at,

            "road":
                ROAD_NAME,

            "averageSpeed":
                average_speed,

            "linkCount":
                link_count,

            "minSpeed":
                min_speed,

            "maxSpeed":
                max_speed,

            "sourceUpdatedAt":
                source_updated_at,

            "sourceStatus":
                source_status,
        }


        # -------------------------
        # DB 저장
        # -------------------------

        with open_db() as conn:

            save_successful_daily(
                conn,
                data,
            )

            save_attempt(
                conn,
                status=source_status,
                message=(
                    "외부 수집기 "
                    "데이터 정상 수신"
                    if (
                        source_status
                        == "NORMAL"
                    )
                    else (
                        "외부 수집기 "
                        "데이터 수신 - "
                        "원천 데이터 지연"
                    )
                ),
                data=data,
            )


        return {
            "ok":
                True,

            "date":
                local_date.isoformat(),

            "road":
                ROAD_NAME,

            "averageSpeed":
                average_speed,

            "linkCount":
                link_count,

            "sourceStatus":
                source_status,
        }


    # -------------------------
    # 잘못된 요청
    # -------------------------

    except Exception as exc:

        return JSONResponse(
            status_code=400,
            content={
                "ok":
                    False,

                "message":
                    str(exc),
            },
        )

@app.get("/api/test-its")
def test_its():
    import ssl
    import time as time_module
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request as URLRequest, urlopen

    api_key = env("ITS_API_KEY")

    if not api_key:
        return {
            "ok": False,
            "message": "ITS_API_KEY가 설정되지 않았습니다.",
        }

    params = {
        "apiKey": api_key,
        "type": "all",
        "drcType": "all",
        "minX": 128.40,
        "maxX": 128.80,
        "minY": 35.75,
        "maxY": 36.00,
        "getType": "json",
    }

    url = (
        "https://openapi.its.go.kr:9443/trafficInfo?"
        + urlencode(params)
    )

    request = URLRequest(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "aleph-traffic-test",
        },
        method="GET",
    )

    started = time_module.perf_counter()

    try:
        with urlopen(
            request,
            timeout=15,
            context=ssl.create_default_context(),
        ) as response:

            raw = response.read(500)

            elapsed = round(
                time_module.perf_counter() - started,
                2,
            )

            return {
                "ok": True,
                "httpStatus": response.status,
                "elapsedSeconds": elapsed,
                "receivedBytes": len(raw),
                "preview": raw.decode(
                    "utf-8",
                    errors="replace",
                ),
            }

    except HTTPError as exc:
        return {
            "ok": False,
            "type": "HTTP_ERROR",
            "status": exc.code,
            "message": str(exc),
        }

    except URLError as exc:
        return {
            "ok": False,
            "type": "URL_ERROR",
            "message": str(exc.reason),
        }

    except TimeoutError:
        return {
            "ok": False,
            "type": "TIMEOUT",
            "message": "ITS 연결 시간이 초과되었습니다.",
        }

    except Exception as exc:
        return {
            "ok": False,
            "type": type(exc).__name__,
            "message": str(exc),
        }

@app.get("/api/test-its-tcp")
def test_its_tcp():
    import socket
    import time

    host = "openapi.its.go.kr"
    port = 9443

    try:
        ips = socket.gethostbyname_ex(host)

        started = time.perf_counter()

        sock = socket.create_connection(
            (host, port),
            timeout=10,
        )

        elapsed = round(
            time.perf_counter() - started,
            2,
        )

        remote = sock.getpeername()
        sock.close()

        return {
            "ok": True,
            "dns": ips,
            "tcpConnected": True,
            "remote": remote,
            "elapsedSeconds": elapsed,
        }

    except Exception as exc:
        return {
            "ok": False,
            "type": type(exc).__name__,
            "message": str(exc),
        }

@app.get("/api/test-its-tls")
def test_its_tls():
    import socket
    import ssl
    import time

    host = "openapi.its.go.kr"
    port = 9443

    try:
        started = time.perf_counter()

        raw_socket = socket.create_connection(
            (host, port),
            timeout=10,
        )

        context = ssl.create_default_context()

        tls_socket = context.wrap_socket(
            raw_socket,
            server_hostname=host,
        )

        elapsed = round(
            time.perf_counter() - started,
            2,
        )

        result = {
            "ok": True,
            "tlsConnected": True,
            "tlsVersion": tls_socket.version(),
            "cipher": tls_socket.cipher(),
            "elapsedSeconds": elapsed,
        }

        tls_socket.close()

        return result

    except Exception as exc:
        return {
            "ok": False,
            "type": type(exc).__name__,
            "message": str(exc),
        }

@app.get("/api/test-its-raw-http")
def test_its_raw_http():
    import socket
    import ssl
    import time
    from urllib.parse import urlencode

    host = "openapi.its.go.kr"
    port = 9443

    api_key = env("ITS_API_KEY")

    if not api_key:
        return {
            "ok": False,
            "message": "ITS_API_KEY가 설정되지 않았습니다.",
        }

    params = urlencode({
        "apiKey": api_key,
        "type": "all",
        "drcType": "all",
        "minX": 128.40,
        "maxX": 128.80,
        "minY": 35.75,
        "maxY": 36.00,
        "getType": "json",
    })

    path = f"/trafficInfo?{params}"

    try:
        started = time.perf_counter()

        raw_socket = socket.create_connection(
            (host, port),
            timeout=10,
        )

        context = ssl.create_default_context()

        tls_socket = context.wrap_socket(
            raw_socket,
            server_hostname=host,
        )

        tls_socket.settimeout(10)

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:9443\r\n"
            "User-Agent: curl/8.0\r\n"
            "Accept: */*\r\n"
            "Connection: close\r\n"
            "\r\n"
        )

        tls_socket.sendall(
            request.encode("ascii")
        )

        data = tls_socket.recv(1000)

        elapsed = round(
            time.perf_counter() - started,
            2,
        )

        tls_socket.close()

        return {
            "ok": True,
            "receivedBytes": len(data),
            "elapsedSeconds": elapsed,
            "preview": data.decode(
                "utf-8",
                errors="replace",
            ),
        }

    except Exception as exc:
        return {
            "ok": False,
            "type": type(exc).__name__,
            "message": str(exc),
        }
