"""Shared ITS parsing, validation, sampling, and summarization logic."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ROAD_NAME = "달구벌대로"

BBOX = {
    "minX": 128.40,
    "maxX": 128.80,
    "minY": 35.75,
    "maxY": 36.00,
}

SOURCE_DELAY_MINUTES = 30
SOURCE_STALE_RATIO_THRESHOLD = 0.30
MAX_VALID_SPEED_KMH = 200.0
SOURCE_SAMPLE_LIMIT = 5


class TrafficDataError(RuntimeError):
    """Expected external-source/data failure with a stable public failure type."""

    def __init__(self, failure_type: str, message: str):
        super().__init__(message)
        self.failure_type = failure_type


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_its_datetime(value: object) -> datetime | None:
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
            return datetime.strptime(text, fmt).replace(tzinfo=KST)
        except ValueError:
            continue

    return None


def find_traffic_items(payload: object) -> list[dict]:
    """Find a list of ITS item dictionaries even if the response is nested."""
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list) and all(isinstance(item, dict) for item in items):
            return items

        for value in payload.values():
            found = find_traffic_items(value)
            if found:
                return found

    elif isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            return payload

        for value in payload:
            found = find_traffic_items(value)
            if found:
                return found

    return []


def detect_api_error(payload: object) -> None:
    if not isinstance(payload, dict):
        return

    header = payload.get("header")
    if not isinstance(header, dict):
        return

    result_code = str(header.get("resultCode", "")).strip()
    result_msg = str(header.get("resultMsg", "")).strip()

    if result_code and result_code not in {"0", "00"}:
        raise TrafficDataError(
            "HTTP_ERROR",
            f"ITS API 오류 {result_code}: {result_msg or '알 수 없는 오류'}",
        )


def _link_id(item: dict) -> str | None:
    value = item.get("linkId")
    if value is None:
        value = item.get("linkID")
    if value is None:
        value = item.get("link_id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _even_sample(records: list[dict], limit: int = SOURCE_SAMPLE_LIMIT) -> list[dict]:
    """Pick at most ``limit`` deterministic, evenly spaced source records."""
    if not records:
        return []
    if len(records) <= limit:
        return records.copy()
    if limit <= 1:
        return [records[0]]

    last = len(records) - 1
    indices = [round(i * last / (limit - 1)) for i in range(limit)]
    # round() can theoretically produce duplicates for tiny inputs; keep order unique.
    unique_indices = list(dict.fromkeys(indices))
    return [records[index] for index in unique_indices]


def summarize_traffic(
    payload: object,
    *,
    captured_at: datetime | None = None,
) -> dict:
    """Extract Dalgubeol-daero links and return one daily summary payload.

    Freshness is decided from all links that expose a source timestamp. A source is
    DELAYED when at least 30% of those links are 30 minutes or more behind capture.
    Up to five evenly spaced real source-link samples are retained for verification.
    """
    detect_api_error(payload)
    items = find_traffic_items(payload)
    if not items:
        raise TrafficDataError("EMPTY_DATA", "ITS 응답에 교통 데이터가 없습니다.")

    valid_records: list[dict] = []
    created_times: list[datetime] = []

    for item in items:
        road_name = str(item.get("roadName", "")).strip()
        if ROAD_NAME not in road_name:
            continue

        try:
            speed = float(item.get("speed"))
        except (TypeError, ValueError):
            continue

        if not (0 < speed <= MAX_VALID_SPEED_KMH):
            continue

        created_at = parse_its_datetime(item.get("createdDate"))
        if created_at is not None:
            created_times.append(created_at)

        valid_records.append(
            {
                "roadName": road_name,
                "linkId": _link_id(item),
                "speed": speed,
                "createdAt": created_at,
            }
        )

    if not valid_records:
        raise TrafficDataError(
            "EMPTY_DATA",
            f"ITS 응답에서 {ROAD_NAME}의 유효한 속도 데이터를 찾지 못했습니다.",
        )

    captured_at = captured_at or now_kst()
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=KST)
    else:
        captured_at = captured_at.astimezone(KST)

    source_updated_at = max(created_times) if created_times else None
    stale_count = 0

    for created_at in created_times:
        age_minutes = (
            captured_at - created_at.astimezone(KST)
        ).total_seconds() / 60
        if age_minutes >= SOURCE_DELAY_MINUTES:
            stale_count += 1

    timestamp_count = len(created_times)
    stale_ratio = stale_count / timestamp_count if timestamp_count else 0.0
    source_status = (
        "DELAYED"
        if timestamp_count and stale_ratio >= SOURCE_STALE_RATIO_THRESHOLD
        else "NORMAL"
    )

    speeds = [record["speed"] for record in valid_records]
    samples = []
    for record in _even_sample(valid_records):
        created_at = record["createdAt"]
        samples.append(
            {
                "roadName": record["roadName"],
                "linkId": record["linkId"],
                "speed": round(record["speed"], 1),
                "sourceUpdatedAt": created_at.isoformat() if created_at else None,
            }
        )

    return {
        "localDate": captured_at.date().isoformat(),
        "capturedAt": captured_at.isoformat(),
        "road": ROAD_NAME,
        "averageSpeed": round(sum(speeds) / len(speeds), 1),
        "linkCount": len(speeds),
        "minSpeed": round(min(speeds), 1),
        "maxSpeed": round(max(speeds), 1),
        "sourceUpdatedAt": (
            source_updated_at.isoformat() if source_updated_at else None
        ),
        "sourceStatus": source_status,
        "sourceTimestampCount": timestamp_count,
        "staleLinkCount": stale_count,
        "staleRatio": round(stale_ratio, 4),
        "samples": samples,
    }
