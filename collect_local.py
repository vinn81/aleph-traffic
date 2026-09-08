"""달구벌대로 교통정보 로컬 수집기.

매일 09:00 KST에 국내 일반 회선에서 실행한다.
ITS 원본 데이터는 로컬에서 조회하고, 요약 결과와 검증용 원천 샘플만
Vercel API로 전송한다.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urlencode

from traffic_core import (
    BBOX,
    ROAD_NAME,
    TrafficDataError,
    now_kst,
    summarize_traffic,
)

ITS_URL = "https://openapi.its.go.kr:9443/trafficInfo"
HTTP_TIMEOUT_SECONDS = 60
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class CollectorFailure(RuntimeError):
    def __init__(self, failure_type: str | None, message: str):
        super().__init__(message)
        self.failure_type = failure_type


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"환경변수 {name} 가 설정되지 않았습니다.")
    return value


def ingest_secret() -> str:
    value = (
        os.environ.get("INGEST_SECRET", "").strip()
        or os.environ.get("CRON_SECRET", "").strip()
    )
    if not value:
        raise RuntimeError(
            "환경변수 INGEST_SECRET가 설정되지 않았습니다. "
            "기존 CRON_SECRET도 사용할 수 있습니다."
        )
    return value


def attempt_url() -> str:
    explicit = os.environ.get("ATTEMPT_URL", "").strip()
    if explicit:
        return explicit

    ingest = require_env("INGEST_URL").rstrip("/")
    if ingest.endswith("/ingest"):
        return ingest.removesuffix("/ingest") + "/attempt"
    return ingest + "/attempt"


def fetch_its() -> dict:
    params = {
        "apiKey": require_env("ITS_API_KEY"),
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
    request = urllib.request.Request(url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise CollectorFailure(
            "HTTP_ERROR",
            f"ITS API HTTP 오류가 발생했습니다. 상태코드: {exc.code}",
        ) from None
    except (TimeoutError, socket.timeout):
        raise CollectorFailure("TIMEOUT", "ITS API 응답 시간이 초과되었습니다.") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise CollectorFailure("TIMEOUT", "ITS API 응답 시간이 초과되었습니다.") from None
        raise CollectorFailure(
            "HTTP_ERROR",
            "ITS API에 연결하지 못했습니다.",
        ) from None

    if len(raw) > MAX_RESPONSE_BYTES:
        raise CollectorFailure(
            "HTTP_ERROR",
            "ITS 응답 크기가 허용 범위를 초과했습니다.",
        )

    print(f"      수신 {len(raw):,} bytes")

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectorFailure(
            "INVALID_JSON",
            "ITS JSON 응답을 해석할 수 없습니다.",
        ) from exc


def post_json(url: str, data: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ingest_secret()}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {"ok": True}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"서버 전송 실패 HTTP {exc.code}: {body}") from None


def send_summary(data: dict) -> dict:
    url = require_env("INGEST_URL")
    print(f"[3/3] 요약 전송 중 -> {url}")
    return post_json(url, data)


def report_failure(message: str, failure_type: str | None = None) -> None:
    """Best-effort failure reporting without API keys or secret values."""
    attempted_at = now_kst()
    payload = {
        "localDate": attempted_at.date().isoformat(),
        "attemptedAt": attempted_at.isoformat(),
        "status": "FAILED",
        "failureType": failure_type,
        "message": message[:500],
    }

    try:
        post_json(attempt_url(), payload)
    except Exception as report_exc:
        print(f"[경고] 실패 이력 전송도 실패했습니다: {report_exc}", file=sys.stderr)


def main() -> int:
    started = now_kst()
    print(f"=== 수집 시작 {started.strftime('%Y-%m-%d %H:%M:%S')} KST ===")

    try:
        payload = fetch_its()
        print(f"[2/3] {ROAD_NAME} 통계 계산 중...")
        data = summarize_traffic(payload, captured_at=started)

        print(
            f"      링크 {data['linkCount']}건 / "
            f"평균 {data['averageSpeed']} / "
            f"최소 {data['minSpeed']} / 최대 {data['maxSpeed']} / "
            f"지연 링크 {data['staleLinkCount']}/{data['sourceTimestampCount']} / "
            f"상태 {data['sourceStatus']} / "
            f"원천 샘플 {len(data['samples'])}건"
        )

        result = send_summary(data)

    except CollectorFailure as exc:
        print(f"\n[실패:{exc.failure_type}] {exc}", file=sys.stderr)
        report_failure(str(exc), exc.failure_type)
        return 1
    except TrafficDataError as exc:
        print(f"\n[실패:{exc.failure_type}] {exc}", file=sys.stderr)
        report_failure(str(exc), exc.failure_type)
        return 1
    except Exception as exc:
        # 서버 인증/저장 오류나 로컬 설정 오류 등은 외부 ITS 5종과 구분한다.
        message = str(exc)
        print(f"\n[실패] {message}", file=sys.stderr)
        report_failure(message, None)
        return 1

    print("\n[성공] 서버 응답:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
