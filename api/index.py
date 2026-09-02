import gzip
import hmac
import json
import os
import socket
import ssl
from datetime import datetime, time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse


app = FastAPI(
    title="달구벌 NOW API",
    version="4.0",
)


# =========================================================
# 기본 설정
# =========================================================

KST = ZoneInfo("Asia/Seoul")

ROAD_NAME = "달구벌대로"

ITS_HOST = "openapi.its.go.kr"
ITS_PORT = 9443
ITS_PATH = "/trafficInfo"

BBOX = {
    "minX": 128.40,
    "maxX": 128.80,
    "minY": 35.75,
    "maxY": 36.00,
}

EXPECTED_HOUR_KST = 9
EXPECTED_MINUTE_KST = 0

SOURCE_DELAY_MINUTES = 30

CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 45

# ITS 응답이 비정상적으로 너무 큰 경우 방지
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


# =========================================================
# 환경변수 / 시간
# =========================================================

def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def db_url() -> str:
    return (
        env("DATABASE_URL")
        or env("POSTGRES_URL")
    )


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_its_datetime(value) -> datetime | None:

    if not value:
        return None

    text = str(value).strip()

    formats = (
        "%Y%m%d%H%M%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    )

    for fmt in formats:

        try:

            parsed = datetime.strptime(
                text,
                fmt,
            )

            return parsed.replace(
                tzinfo=KST
            )

        except ValueError:
            continue

    return None


# =========================================================
# DB
# =========================================================

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


# =========================================================
# 일별 데이터 저장
# =========================================================

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


# =========================================================
# 수집 시도 기록
# =========================================================

def save_attempt(
    conn,
    status: str,
    message: str | None = None,
    data: dict | None = None,
):

    ensure_tables(conn)

    attempted_at = now_kst()

    if (
        data
        and data.get("localDate")
    ):

        local_date = data[
            "localDate"
        ]

    else:

        local_date = (
            attempted_at.date()
        )

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
            local_date,
            status,
            message,

            (
                data.get(
                    "averageSpeed"
                )
                if data
                else None
            ),

            (
                data.get(
                    "linkCount"
                )
                if data
                else None
            ),

            (
                data.get(
                    "sourceUpdatedAt"
                )
                if data
                else None
            ),
        ),
    )


def record_failed_attempt(
    message: str,
):

    try:

        with open_db() as conn:

            save_attempt(
                conn,
                status="FAILED",
                message=message,
            )

    except Exception:
        pass


# =========================================================
# DB 데이터 변환
# =========================================================

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


# =========================================================
# 예정 수집시간 확인
# =========================================================

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


# =========================================================
# HTTP chunked 응답 해제
# =========================================================

def decode_chunked_body(
    body: bytes,
) -> bytes:

    output = bytearray()

    position = 0

    while True:

        line_end = body.find(
            b"\r\n",
            position,
        )

        if line_end < 0:

            raise RuntimeError(
                "ITS chunked 응답 형식이 올바르지 않습니다."
            )

        size_line = body[
            position:line_end
        ]

        size_line = size_line.split(
            b";",
            1,
        )[0].strip()

        try:

            chunk_size = int(
                size_line,
                16,
            )

        except ValueError as exc:

            raise RuntimeError(
                "ITS chunk 크기를 해석할 수 없습니다."
            ) from exc

        position = line_end + 2

        if chunk_size == 0:
            break

        chunk_end = (
            position
            + chunk_size
        )

        if chunk_end > len(body):

            raise RuntimeError(
                "ITS chunked 응답이 중간에 끊겼습니다."
            )

        output.extend(
            body[
                position:chunk_end
            ]
        )

        position = chunk_end

        if (
            body[
                position:
                position + 2
            ]
            != b"\r\n"
        ):

            raise RuntimeError(
                "ITS chunk 구분자가 올바르지 않습니다."
            )

        position += 2

    return bytes(output)


# =========================================================
# ITS 문자 인코딩 처리
# =========================================================

def decode_response_text(
    body: bytes,
) -> str:

    # 정상 UTF-8이면 그대로 사용
    try:

        return body.decode(
            "utf-8"
        )

    except UnicodeDecodeError:
        pass

    # ITS 응답에서 한글이 CP949로 오는 경우 대비
    try:

        return body.decode(
            "cp949"
        )

    except UnicodeDecodeError:
        pass

    return body.decode(
        "euc-kr",
        errors="replace",
    )


# =========================================================
# RAW HTTPS GET
#
# urllib 방식 대신
# 실제 Vercel에서 성공한 방식 사용
# =========================================================

def raw_https_get_json(
    host: str,
    port: int,
    path: str,
):

    request_text = (

        f"GET {path} HTTP/1.1\r\n"

        f"Host: {host}:{port}\r\n"

        "User-Agent: curl/8.0\r\n"

        "Accept: application/json\r\n"

        "Accept-Encoding: identity\r\n"

        "Connection: close\r\n"

        "\r\n"
    )

    raw_socket = None
    tls_socket = None

    try:

        # -------------------------
        # TCP 연결
        # -------------------------

        raw_socket = (
            socket.create_connection(
                (
                    host,
                    port,
                ),
                timeout=(
                    CONNECT_TIMEOUT_SECONDS
                ),
            )
        )

        # -------------------------
        # TLS 연결
        # -------------------------

        context = (
            ssl.create_default_context()
        )

        tls_socket = (
            context.wrap_socket(
                raw_socket,
                server_hostname=host,
            )
        )

        tls_socket.settimeout(
            READ_TIMEOUT_SECONDS
        )

        # -------------------------
        # HTTP GET 전송
        # -------------------------

        tls_socket.sendall(
            request_text.encode(
                "ascii"
            )
        )

        # -------------------------
        # 응답 전체 수신
        # -------------------------

        chunks = []

        total_bytes = 0

        while True:

            part = tls_socket.recv(
                65536
            )

            if not part:
                break

            total_bytes += len(
                part
            )

            if (
                total_bytes
                > MAX_RESPONSE_BYTES
            ):

                raise RuntimeError(
                    "ITS 응답 크기가 "
                    "허용 범위를 초과했습니다."
                )

            chunks.append(
                part
            )

        raw_response = b"".join(
            chunks
        )

    finally:

        if tls_socket is not None:

            try:
                tls_socket.close()

            except Exception:
                pass

        elif raw_socket is not None:

            try:
                raw_socket.close()

            except Exception:
                pass

    if not raw_response:

        raise RuntimeError(
            "ITS 서버가 빈 응답을 반환했습니다."
        )

    # -------------------------
    # HTTP 헤더 / Body 분리
    # -------------------------

    try:

        header_bytes, body = (
            raw_response.split(
                b"\r\n\r\n",
                1,
            )
        )

    except ValueError as exc:

        raise RuntimeError(
            "ITS HTTP 응답 형식이 올바르지 않습니다."
        ) from exc

    header_text = (
        header_bytes.decode(
            "iso-8859-1",
            errors="replace",
        )
    )

    header_lines = (
        header_text.split(
            "\r\n"
        )
    )

    status_line = (
        header_lines[0]
    )

    # -------------------------
    # HTTP 상태코드
    # -------------------------

    try:

        status_code = int(
            status_line.split(
                " ",
                2,
            )[1]
        )

    except Exception as exc:

        raise RuntimeError(
            "ITS HTTP 상태를 "
            f"해석할 수 없습니다: {status_line}"
        ) from exc

    # -------------------------
    # HTTP 헤더 파싱
    # -------------------------

    headers = {}

    for line in header_lines[1:]:

        if ":" not in line:
            continue

        key, value = line.split(
            ":",
            1,
        )

        headers[
            key.strip().lower()
        ] = value.strip()

    # -------------------------
    # chunked 응답 처리
    # -------------------------

    transfer_encoding = (
        headers.get(
            "transfer-encoding",
            "",
        ).lower()
    )

    if (
        "chunked"
        in transfer_encoding
    ):

        body = (
            decode_chunked_body(
                body
            )
        )

    # -------------------------
    # gzip 응답 처리
    # -------------------------

    content_encoding = (
        headers.get(
            "content-encoding",
            "",
        ).lower()
    )

    if content_encoding == "gzip":

        body = gzip.decompress(
            body
        )

    # -------------------------
    # HTTP 오류
    # -------------------------

    if not (
        200
        <= status_code
        < 300
    ):

        preview = (
            decode_response_text(
                body[:1000]
            )
        )

        raise RuntimeError(
            f"ITS HTTP 오류 {status_code}: "
            f"{preview}"
        )

    # -------------------------
    # 문자 인코딩
    # -------------------------

    text = (
        decode_response_text(
            body
        )
    )

    # -------------------------
    # JSON
    # -------------------------

    try:

        return json.loads(
            text
        )

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "ITS JSON 응답을 "
            "해석할 수 없습니다."
        ) from exc


# =========================================================
# ITS items 찾기
# =========================================================

def find_traffic_items(
    payload,
) -> list:

    if isinstance(
        payload,
        dict,
    ):

        items = payload.get(
            "items"
        )

        if isinstance(
            items,
            list,
        ):
            return items

        for value in (
            payload.values()
        ):

            found = (
                find_traffic_items(
                    value
                )
            )

            if found:
                return found

    elif isinstance(
        payload,
        list,
    ):

        for value in payload:

            found = (
                find_traffic_items(
                    value
                )
            )

            if found:
                return found

    return []


# =========================================================
# ITS 오류 확인
# =========================================================

def detect_api_error(
    payload,
):

    if not isinstance(
        payload,
        dict,
    ):
        return

    header = payload.get(
        "header"
    )

    if not isinstance(
        header,
        dict,
    ):
        return

    result_code = str(
        header.get(
            "resultCode",
            ""
        )
    ).strip()

    result_msg = str(
        header.get(
            "resultMsg",
            ""
        )
    ).strip()

    if (
        result_code
        and result_code != "0"
    ):

        raise RuntimeError(
            "ITS API 오류 "
            f"{result_code}: "
            f"{result_msg or '알 수 없는 오류'}"
        )


# =========================================================
# 달구벌대로 데이터 수집
# =========================================================

def fetch_dalgubeol_traffic():

    api_key = env(
        "ITS_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "ITS_API_KEY가 설정되지 않았습니다."
        )

    params = {

        "apiKey":
            api_key,

        "type":
            "all",

        "drcType":
            "all",

        "minX":
            BBOX["minX"],

        "maxX":
            BBOX["maxX"],

        "minY":
            BBOX["minY"],

        "maxY":
            BBOX["maxY"],

        "getType":
            "json",
    }

    path = (
        ITS_PATH
        + "?"
        + urlencode(
            params
        )
    )

    payload = (
        raw_https_get_json(
            ITS_HOST,
            ITS_PORT,
            path,
        )
    )

    detect_api_error(
        payload
    )

    items = (
        find_traffic_items(
            payload
        )
    )

    if not items:

        raise RuntimeError(
            "ITS 응답에 "
            "교통 데이터가 없습니다."
        )

    selected = []

    # -------------------------
    # 달구벌대로만 추출
    # -------------------------

    for item in items:

        if not isinstance(
            item,
            dict,
        ):
            continue

        road_name = str(
            item.get(
                "roadName",
                ""
            )
        ).strip()

        if (
            ROAD_NAME
            not in road_name
        ):
            continue

        # -------------------------
        # 속도 값
        # -------------------------

        try:

            speed = float(
                item.get(
                    "speed"
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        # 비정상 속도 제외
        if not (
            0
            < speed
            <= 200
        ):
            continue

        created_at = (
            parse_its_datetime(
                item.get(
                    "createdDate"
                )
            )
        )

        selected.append(
            {
                "speed":
                    speed,

                "createdAt":
                    created_at,
            }
        )

    if not selected:

        raise RuntimeError(
            "ITS 응답에서 "
            "달구벌대로의 유효한 "
            "속도 데이터를 찾지 못했습니다."
        )

    # -------------------------
    # 속도 통계
    # -------------------------

    speeds = [

        row["speed"]

        for row in selected
    ]

    created_times = [

        row["createdAt"]

        for row in selected

        if (
            row["createdAt"]
            is not None
        )
    ]

    captured_at = (
        now_kst()
    )

    # 가장 최근 ITS 데이터 시각
    source_updated_at = (

        max(created_times)

        if created_times

        else None
    )

    source_status = (
        "NORMAL"
    )

    # -------------------------
    # 원천 데이터 지연 확인
    # -------------------------

    if source_updated_at:

        age_minutes = (

            (
                captured_at
                -
                source_updated_at.astimezone(
                    KST
                )
            ).total_seconds()

            / 60
        )

        if (
            age_minutes
            >= SOURCE_DELAY_MINUTES
        ):

            source_status = (
                "DELAYED"
            )

    average_speed = round(

        sum(speeds)
        / len(speeds),

        1,
    )

    return {

        "localDate":
            captured_at.date(),

        "capturedAt":
            captured_at,

        "road":
            ROAD_NAME,

        "averageSpeed":
            average_speed,

        "linkCount":
            len(speeds),

        "minSpeed":
            round(
                min(speeds),
                1,
            ),

        "maxSpeed":
            round(
                max(speeds),
                1,
            ),

        "sourceUpdatedAt":
            source_updated_at,

        "sourceStatus":
            source_status,
    }


# =========================================================
# Vercel Cron 인증
# =========================================================

def verify_cron_secret(
    authorization: str | None,
):

    secret = env(
        "CRON_SECRET"
    )

    if not secret:

        raise RuntimeError(
            "CRON_SECRET가 설정되지 않았습니다."
        )

    expected = (
        f"Bearer {secret}"
    )

    return hmac.compare_digest(
        authorization or "",
        expected,
    )


# =========================================================
# API 기본 정보
# =========================================================

@app.get("/api")
def api_index():

    return {

        "name":
            "달구벌 NOW Daily API",

        "version":
            "4.0",

        "endpoints": [
            "/api/health",
            "/api/dashboard",
            "/api/history",
            "/api/collect",
        ],
    }


# =========================================================
# Health Check
# =========================================================

@app.get("/api/health")
def health():

    database_configured = bool(
        db_url()
    )

    its_key_configured = bool(
        env(
            "ITS_API_KEY"
        )
    )

    cron_secret_configured = bool(
        env(
            "CRON_SECRET"
        )
    )

    return {

        "ok":
            (
                database_configured
                and
                its_key_configured
                and
                cron_secret_configured
            ),

        "databaseConfigured":
            database_configured,

        "itsApiKeyConfigured":
            its_key_configured,

        "cronSecretConfigured":
            cron_secret_configured,

        "runtime":
            "Vercel Python / FastAPI",
    }


# =========================================================
# 실제 ITS 자동수집
# =========================================================

@app.get("/api/collect")
def collect(
    authorization: str | None = Header(
        default=None
    ),
):

    try:

        # -------------------------
        # Cron 인증
        # -------------------------

        if not verify_cron_secret(
            authorization
        ):

            return JSONResponse(
                status_code=401,
                content={
                    "ok":
                        False,

                    "message":
                        "Cron 인증에 실패했습니다.",
                },
            )

        # -------------------------
        # ITS 호출
        # -------------------------

        data = (
            fetch_dalgubeol_traffic()
        )

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
                status=(
                    data[
                        "sourceStatus"
                    ]
                ),
                message=(
                    "Vercel 자동 수집 성공"

                    if (
                        data[
                            "sourceStatus"
                        ]
                        == "NORMAL"
                    )

                    else (
                        "Vercel 자동 수집 성공 - "
                        "ITS 원천 데이터 지연"
                    )
                ),
                data=data,
            )

        # -------------------------
        # 결과
        # -------------------------

        return {

            "ok":
                True,

            "date":
                data[
                    "localDate"
                ].isoformat(),

            "road":
                data[
                    "road"
                ],

            "averageSpeed":
                data[
                    "averageSpeed"
                ],

            "linkCount":
                data[
                    "linkCount"
                ],

            "minSpeed":
                data[
                    "minSpeed"
                ],

            "maxSpeed":
                data[
                    "maxSpeed"
                ],

            "capturedAt":
                data[
                    "capturedAt"
                ].isoformat(),

            "sourceUpdatedAt":
                (
                    data[
                        "sourceUpdatedAt"
                    ].isoformat()

                    if (
                        data[
                            "sourceUpdatedAt"
                        ]
                    )

                    else None
                ),

            "sourceStatus":
                data[
                    "sourceStatus"
                ],
        }

    except Exception as exc:

        message = str(
            exc
        )

        # 실패도 DB에 기록
        record_failed_attempt(
            message
        )

        return JSONResponse(
            status_code=500,
            content={

                "ok":
                    False,

                "status":
                    "FAILED",

                "message":
                    message,
            },
        )


# =========================================================
# 과거 기록 조회
# =========================================================

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


# =========================================================
# 대시보드
# =========================================================

@app.get("/api/dashboard")
def dashboard():

    current = (
        now_kst()
    )

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


    # =====================================================
    # 오늘 실제 기록
    # =====================================================

    today_record = next(
        (
            row
            for row in daily

            if (
                row["date"]
                ==
                current.date().isoformat()
            )
        ),
        None,
    )


    # =====================================================
    # 마지막 정상 기록
    # =====================================================

    latest_normal = next(
        (
            row
            for row in daily

            if (
                row[
                    "sourceStatus"
                ]
                == "NORMAL"
            )
        ),
        None,
    )


    # =====================================================
    # 오늘보다 이전의 가장 최근 실제 날짜
    # =====================================================

    previous_record = None

    if today_record:

        previous_record = next(
            (
                row
                for row in daily

                if (
                    row["date"]
                    <
                    today_record["date"]
                )
            ),
            None,
        )


    # =====================================================
    # 오늘 데이터 존재
    # =====================================================

    if today_record:

        status = (

            "DELAYED"

            if (
                today_record[
                    "sourceStatus"
                ]
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
                "30분 이상 오래된 상태입니다."
            )

        shown_record = (
            today_record
        )

        is_fallback = False


    # =====================================================
    # 오늘 수집 실패
    # =====================================================

    elif (
        today_attempt
        and
        today_attempt[
            "status"
        ]
        == "FAILED"
    ):

        status = "ERROR"

        message = (
            "오늘 교통 데이터 수집에 "
            "실패했습니다. "
            "마지막 정상 기록을 "
            "대신 표시합니다."
        )

        shown_record = (
            latest_normal
        )

        is_fallback = bool(
            latest_normal
        )


    # =====================================================
    # 09:30 이후인데 오늘 데이터 없음
    # =====================================================

    elif expected_collect_passed(
        current
    ):

        status = "MISSING"

        message = (
            "오늘 오전 9:30 예정된 "
            "교통 데이터가 아직 없습니다. "
            "마지막 정상 기록이 있으면 "
            "대신 표시합니다."
        )

        shown_record = (
            latest_normal
        )

        is_fallback = bool(
            latest_normal
        )


    # =====================================================
    # 아직 09:30 전
    # =====================================================

    else:

        status = "WAITING"

        message = (
            "오늘 오전 9:30 "
            "자동 수집 전입니다. "
            "마지막 정상 기록이 있으면 "
            "참고값으로 표시합니다."
        )

        shown_record = (
            latest_normal
        )

        is_fallback = bool(
            latest_normal
        )


    # =====================================================
    # 직전 실제 날짜와 비교
    # =====================================================

    comparison = None

    if (
        today_record
        and
        previous_record
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


    # =====================================================
    # 최종 응답
    # =====================================================

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
