import json
import os
import ssl
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen


KST = ZoneInfo("Asia/Seoul")

ITS_API_URL = "https://openapi.its.go.kr:9443/trafficInfo"
INGEST_URL = "https://aleph-traffic.vercel.app/api/ingest"

ROAD_KEYWORD = "달구벌대로"

BBOX = {
    "minX": 128.40,
    "maxX": 128.80,
    "minY": 35.75,
    "maxY": 36.00,
}


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
    lower = {
        str(key).lower(): value
        for key, value in item.items()
    }

    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]

    return None


def find_traffic_items(obj):
    found = []

    if isinstance(obj, dict):
        keys = {str(key).lower() for key in obj.keys()}

        if (
            ("roadname" in keys or "road_name" in keys)
            and "speed" in keys
        ):
            found.append(obj)

        for value in obj.values():
            found.extend(find_traffic_items(value))

    elif isinstance(obj, list):
        for value in obj:
            found.extend(find_traffic_items(value))

    return found


def fetch_traffic():
    api_key = os.environ["ITS_API_KEY"]

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

    url = ITS_API_URL + "?" + urlencode(params)

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Dalgubeol-GitHub-Collector/1.0",
        },
    )

    with urlopen(
        request,
        timeout=30,
        context=ssl.create_default_context(),
    ) as response:
        data = json.loads(
            response.read().decode("utf-8")
        )

    items = find_traffic_items(data)

    road_items = []

    for item in items:
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
                "speed": speed,
                "createdDate": str(
                    get_value(
                        item,
                        "createdDate",
                        "created_date",
                    )
                    or ""
                ),
            }
        )

    if not road_items:
        raise RuntimeError(
            "달구벌대로 교통 데이터를 찾지 못했습니다."
        )

    speeds = [item["speed"] for item in road_items]

    source_times = [
        parse_its_datetime(item["createdDate"])
        for item in road_items
        if item["createdDate"]
    ]

    source_times = [
        dt for dt in source_times
        if dt is not None
    ]

    latest_source = (
        max(source_times)
        if source_times
        else None
    )

    captured_at = datetime.now(KST)

    source_status = "NORMAL"

    if latest_source:
        age_minutes = (
            captured_at - latest_source
        ).total_seconds() / 60

        if age_minutes >= 30:
            source_status = "DELAYED"

    return {
        "capturedAt": captured_at.isoformat(),
        "averageSpeed": round(
            statistics.fmean(speeds), 1
        ),
        "linkCount": len(speeds),
        "minSpeed": round(min(speeds), 1),
        "maxSpeed": round(max(speeds), 1),
        "sourceUpdatedAt": (
            latest_source.isoformat()
            if latest_source
            else None
        ),
        "sourceStatus": source_status,
    }


def send_to_vercel(payload):
    secret = os.environ["INGEST_SECRET"]

    body = json.dumps(payload).encode("utf-8")

    request = Request(
        INGEST_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(
        request,
        timeout=30,
        context=ssl.create_default_context(),
    ) as response:
        result = response.read().decode("utf-8")

    print(result)


if __name__ == "__main__":
    traffic = fetch_traffic()

    print(
        "달구벌대로 평균속도:",
        traffic["averageSpeed"],
        "km/h",
    )

    print(
        "수집 링크:",
        traffic["linkCount"],
    )

    send_to_vercel(traffic)
