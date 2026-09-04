"""
달구벌대로 교통정보 로컬 수집기

ITS가 클라우드 데이터센터 IP를 차단하므로 국내 일반 회선에서
데이터를 수집한 뒤, 요약 결과만 Vercel /api/ingest 로 전송한다.

실행:
    python collect_local.py

환경변수 (필수):
    ITS_API_KEY   ITS 오픈API 인증키
    CRON_SECRET   Vercel에 설정된 값과 동일해야 함
    INGEST_URL    예: https://your-app.vercel.app/api/ingest
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

# =========================================================
# 설정 (api/index.py 와 동일하게 유지할 것)
# =========================================================

KST = ZoneInfo("Asia/Seoul")

ROAD_NAME = "달구벌대로"

ITS_URL = "https://openapi.its.go.kr:9443/trafficInfo"

BBOX = {
    "minX": 128.40,
    "maxX": 128.80,
    "minY": 35.75,
    "maxY": 36.00,
}

SOURCE_DELAY_MINUTES = 30

HTTP_TIMEOUT = 60


# =========================================================
# 유틸
# =========================================================

def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"환경변수 {name} 가 설정되지 않았습니다.")
    return value


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_its_datetime(value):
    if not value:
        return None

    text = str(value).strip()

    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=KST)
        except ValueError:
            continue

    return None


def find_traffic_items(payload):
    """ITS 응답 구조가 바뀌어도 items 배열을 찾아낸다."""
    if isinstance(payload, dict):
        body = payload.get("body")
        if isinstance(body, dict):
            items = body.get("items")
            if isinstance(items, list):
                return items

        items = payload.get("items")
        if isinstance(items, list):
            return items

        for value in payload.values():
            found = find_traffic_items(value)
            if found:
                return found

    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            return payload

    return []


# =========================================================
# 1. ITS 호출
# =========================================================

def fetch_its() -> dict:
    params = {
        "apiKey": env("ITS_API_KEY"),
        "type": "all",
        "drcType": "all",
        "minX": BBOX["minX"],
        "maxX": BBOX["maxX"],
        "minY": BBOX["minY"],
        "maxY": BBOX["maxY"],
        "getType": "json",
    }

    url = ITS_URL + "?" + urlencode(params)

    print("[1/3] ITS 호출 중...")

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        raw = response.read()

    print(f"      수신 {len(raw):,} bytes")

    payload = json.loads(raw.decode("utf-8"))

    header = payload.get("header", {})
    result_code = str(header.get("resultCode", "0"))

    if result_code not in ("0", "00"):
        raise RuntimeError(
            f"ITS 오류 resultCode={result_code} "
            f"resultMsg={header.get('resultMsg')}"
        )

    return payload


# =========================================================
# 2. 달구벌대로 추출 및 통계
# =========================================================

def summarize(payload: dict) -> dict:
    items = find_traffic_items(payload)

    if not items:
        raise RuntimeError("ITS 응답에 교통 데이터가 없습니다.")

    print(f"[2/3] 전체 {len(items):,}건에서 {ROAD_NAME} 추출 중...")

    speeds = []
    created_times = []

    for item in items:
        if not isinstance(item, dict):
            continue

        road_name = str(item.get("roadName", "")).strip()

        if ROAD_NAME not in road_name:
            continue

        try:
            speed = float(item.get("speed"))
        except (TypeError, ValueError):
            continue

        # 비정상 속도 제외
        if not (0 < speed <= 200):
            continue

        speeds.append(speed)

        created_at = parse_its_datetime(item.get("createdDate"))
        if created_at is not None:
            created_times.append(created_at)

    if not speeds:
        raise RuntimeError(
            f"ITS 응답에서 {ROAD_NAME}의 유효한 속도 데이터를 찾지 못했습니다."
        )

    captured_at = now_kst()

    source_updated_at = max(created_times) if created_times else None

    source_status = "NORMAL"

    if source_updated_at:
        age_minutes = (
            captured_at - source_updated_at.astimezone(KST)
        ).total_seconds() / 60

        if age_minutes >= SOURCE_DELAY_MINUTES:
            source_status = "DELAYED"

    average_speed = round(sum(speeds) / len(speeds), 1)

    print(
        f"      링크 {len(speeds)}건 / "
        f"평균 {average_speed} / "
        f"최소 {round(min(speeds), 1)} / "
        f"최대 {round(max(speeds), 1)} / "
        f"상태 {source_status}"
    )

    return {
        "localDate": captured_at.date().isoformat(),
        "capturedAt": captured_at.isoformat(),
        "road": ROAD_NAME,
        "averageSpeed": average_speed,
        "linkCount": len(speeds),
        "minSpeed": round(min(speeds), 1),
        "maxSpeed": round(max(speeds), 1),
        "sourceUpdatedAt": (
            source_updated_at.isoformat() if source_updated_at else None
        ),
        "sourceStatus": source_status,
    }


# =========================================================
# 3. Vercel 전송
# =========================================================

def send(data: dict) -> dict:
    url = env("INGEST_URL")
    secret = env("CRON_SECRET")

    print(f"[3/3] 전송 중 -> {url}")

    request = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"전송 실패 HTTP {exc.code}: {body}") from None


# =========================================================
# 진입점
# =========================================================

def main() -> int:
    started = now_kst()
    print(f"=== 수집 시작 {started.strftime('%Y-%m-%d %H:%M:%S')} KST ===")

    try:
        payload = fetch_its()
        data = summarize(payload)
        result = send(data)

    except Exception as exc:
        print(f"\n[실패] {exc}", file=sys.stderr)
        return 1

    print("\n[성공] 서버 응답:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
