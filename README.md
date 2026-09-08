# 달구벌 NOW

대구광역시 **달구벌대로**의 ITS 교통 데이터를 국내 회선에서 **매일 오전 09:00 KST에 한 번 수집**하고, 날짜별 1건으로 Neon Postgres에 저장해 **오늘과 전날**의 평균 통행속도를 비교하는 대시보드입니다.

## 최종 구조

```text
매일 09:00 KST
      ↓
로컬 PC / NAS / 국내 서버 스케줄러
      ↓
collect_local.py
      ↓
ITS 교통소통정보 API
      ↓
달구벌대로 링크 추출 + 통계/지연 판정
      ↓
POST /api/ingest
      ↓
Vercel FastAPI
      ↓
Neon Postgres

사용자 접속
      ↓
/api/dashboard
      ↓
Neon DB 조회
      ↓
오늘 / 전날 비교
```

Vercel은 ITS를 직접 호출하지 않습니다. `vercel.json`에도 Cron 설정이 없습니다.

## 파일 구조

```text
aleph-traffic/
├── api/
│   └── index.py
├── tests/
│   └── test_traffic_core.py
├── collect_local.py
├── traffic_core.py
├── index.html
├── verify.html
├── pyproject.toml
├── vercel.json
├── schema.sql
├── .env.example
├── .gitignore
└── README.md
```

## 1. Vercel 환경변수

Vercel Production 환경에는 다음 값이 필요합니다.

```text
DATABASE_URL
INGEST_SECRET
```

기존에 `CRON_SECRET`을 사용하고 있다면 그대로 두어도 호환됩니다. 새 설정에서는 의미가 더 명확한 `INGEST_SECRET`을 권장합니다.

**Vercel에는 `ITS_API_KEY`가 필요하지 않습니다.** ITS 호출은 로컬 수집기만 수행합니다.

## 2. Neon DB 초기화

배포 전에 Neon SQL Editor에서 `schema.sql`을 한 번 실행합니다.

이 프로젝트는 요청마다 `CREATE TABLE`을 실행하지 않습니다. 스키마 변경은 `schema.sql`로 명시적으로 관리합니다.

확인:

```sql
SELECT * FROM traffic_daily ORDER BY local_date DESC;
SELECT * FROM collection_attempts ORDER BY attempted_at DESC;
SELECT * FROM traffic_source_samples ORDER BY local_date DESC, id ASC;
```

## 3. Vercel 배포

GitHub 저장소를 Vercel에 연결해 배포합니다.

배포 후 확인:

```text
https://YOUR_PROJECT.vercel.app/api/health
```

정상 예시:

```json
{
  "ok": true,
  "databaseConfigured": true,
  "ingestSecretConfigured": true,
  "collectionMode": "LOCAL_INGEST",
  "expectedCollectTimeKST": "09:00",
  "runtime": "Vercel Python / FastAPI"
}
```

## 4. 로컬 수집기 환경변수

수집기를 실행하는 국내 PC/NAS/서버에는 다음 값이 필요합니다.

```text
ITS_API_KEY
INGEST_URL=https://YOUR_PROJECT.vercel.app/api/ingest
INGEST_SECRET=Vercel과_동일한_값
```

기존 `CRON_SECRET`도 `INGEST_SECRET` 대신 사용할 수 있습니다.

선택값:

```text
ATTEMPT_URL=https://YOUR_PROJECT.vercel.app/api/attempt
```

`ATTEMPT_URL`을 생략하면 `INGEST_URL`을 기준으로 자동 계산합니다.

> `.env.example`은 값의 예시일 뿐이며 `collect_local.py`가 `.env` 파일을 자동 로드하지는 않습니다. 운영체제 환경변수 또는 스케줄러 실행 스크립트에서 값을 주입하세요.

## 5. 로컬 수집 테스트

환경변수를 설정한 터미널에서:

```bash
python collect_local.py
```

정상 흐름:

```text
[1/3] ITS 호출
[2/3] 달구벌대로 통계 계산
[3/3] /api/ingest 전송
```

성공하면 같은 날짜의 `traffic_daily` 행이 저장됩니다.

## 6. 매일 09:00 자동 실행

과제 조건에 맞춰 로컬 환경의 스케줄러를 **매일 오전 09:00 KST**로 설정합니다.

Windows라면 작업 스케줄러에서 다음 형태로 등록할 수 있습니다.

```text
트리거: 매일 09:00
프로그램: python.exe
인수: C:\path\to\aleph-traffic\collect_local.py
시작 위치: C:\path\to\aleph-traffic
```

환경변수는 해당 사용자/시스템 환경변수에 등록하거나 별도 실행 스크립트에서 설정합니다.

## 7. 하루 1건 저장 정책

`traffic_daily.local_date`가 PRIMARY KEY이므로 날짜별 행은 최대 1건입니다.

같은 날 재수집 시:

- 기존 값이 `DELAYED`이고 새 값이 `NORMAL`이면 정상값으로 갱신
- 기존 값이 `NORMAL`이고 새 값이 `DELAYED`이면 **기존 NORMAL 값을 유지**
- `NORMAL → NORMAL`, `DELAYED → DELAYED` 재수집은 최신 결과로 갱신

수집 시도 자체는 `collection_attempts`에 별도로 기록됩니다.

## 8. 수집 실패 기록

로컬에서 ITS 호출이나 요약 처리에 실패하면 수집기가 `/api/attempt`로 실패 이력을 전송합니다.

따라서 대시보드는 오전 09:00 이후 데이터가 없을 때 다음을 구분할 수 있습니다.

```text
수집 시도 자체가 실패함 → ERROR
실패 이력도 없고 데이터도 없음 → MISSING
```

실패 이력 전송까지 네트워크 문제로 실패한 경우에는 로컬 콘솔 로그를 확인해야 합니다.

## 9. 전날 대비 비교

비교 대상은 **직전 저장 기록이 아니라 달력상 전날**입니다.

예:

```text
9/6 32.4 km/h
9/7 28.7 km/h
```

9/7 화면:

```text
전날 대비 -3.7 km/h
```

전날 데이터가 없다면 다른 과거 날짜를 대신 비교하지 않고 **비교할 기록 없음**으로 표시합니다.

## 10. 원천 데이터 지연 판정

달구벌대로 링크 하나의 최신 시각만 보는 대신, 생성시각이 있는 링크 전체를 검사합니다.

- 기준 지연시간: 30분
- 생성시각이 있는 링크 중 30% 이상이 30분 이상 오래됨 → `DELAYED`
- 그 외 → `NORMAL`

따라서 일부 최신 링크 하나가 다수의 오래된 링크를 가리는 문제를 줄였습니다.

`sourceUpdatedAt`은 참고용으로 가장 최신 ITS 생성시각을 유지합니다.

## 11. 입력 검증

`/api/ingest`는 Pydantic 모델로 다음을 검증합니다.

- 도로명은 `달구벌대로`로 고정
- 속도는 0 초과 200 km/h 이하
- `minSpeed <= averageSpeed <= maxSpeed`
- 링크 수는 1 이상
- `sourceStatus`는 `NORMAL` 또는 `DELAYED`
- 날짜와 한국시간 기준 `capturedAt` 날짜 일치
- timezone 없는 시각 입력 거부
- 정의되지 않은 추가 필드 거부

## 12. 기록 수 표시

대시보드 차트는 최근 기록만 가져오지만, `쌓인 기록`은 별도 `COUNT(*)`를 사용해 DB의 실제 전체 날짜 수를 표시합니다.

## 13. API

```text
GET  /api/health      설정 상태
GET  /api/dashboard   메인 화면 데이터
GET  /api/history     최근 30개 기록 + 전체 기록일 수
GET  /api/verify      제출 조건 검증용 읽기 전용 증거 API
POST /api/ingest      로컬 수집 성공값 수신 (인증 필요)
POST /api/attempt     로컬 수집 실패 이력 수신 (인증 필요)
```

직접 ITS를 호출하던 `/api/collect`, 공개 TCP 진단용 `/api/test-tcp`는 제거했습니다.

## 14. 데이터 계산 방식

ITS 대구 영역 응답에서 `roadName`에 `달구벌대로`가 포함되고 속도가 0 초과 200 km/h 이하인 링크만 사용합니다.

현재 대표값은 유효 링크 속도의 **산술평균**입니다.

```text
평균 = 유효 링크 speed 합 / 유효 링크 수
```

과제에서 “달구벌대로 링크들의 평균속도”를 기록하는 목적에 맞춘 방식입니다. 향후 링크 길이 데이터가 확보되면 주행시간 기반 가중평균으로 발전시킬 수 있습니다.

## 15. 테스트

공통 교통 데이터 처리 로직 테스트:

```bash
python -m unittest discover -s tests -v
```

테스트에는 다음이 포함됩니다.

- 다른 도로/비정상 속도 제외
- 지연 링크 비율 판정
- 최신 링크 하나가 다수의 오래된 링크를 정상으로 오판하지 않는지 확인

## 보안

- 실제 API Key / DB URL / 비밀값을 GitHub에 커밋하지 마세요.
- `.env`, `collector.env` 등은 `.gitignore`에 포함되어 있습니다.
- 서버 내부 예외 상세는 공개 API 응답으로 그대로 반환하지 않고 Vercel 로그에만 남깁니다.


## 16. 제출 검증 화면

배포 후 다음 주소에서 제출 조건을 한 번에 확인할 수 있습니다.

```text
https://YOUR_PROJECT.vercel.app/verify
```

검증 화면은 다음 다섯 항목을 표시합니다.

1. **실제 공개 원천의 값과 맥락**: 국가교통정보센터 ITS, 달구벌대로, 최신 실제 집계값, 실제 링크 샘플 최대 5건
2. **외부 실패 5종 합성 재생**: `TIMEOUT`, `HTTP_ERROR`, `INVALID_JSON`, `EMPTY_DATA`, `STALE_DATA`
3. **마지막 정상값과 일별 기록 보존**: 실패를 0 km/h로 저장하지 않고 기존 NORMAL을 유지
4. **KST 전날 대비**: 오늘과 달력상 전날 실제 기록 2건이 모두 있을 때만 변화값 계산
5. **비밀값 비노출**: `ITS_API_KEY`, `INGEST_SECRET`, `CRON_SECRET`, `DATABASE_URL`, Authorization 헤더를 검증 응답에 포함하지 않음

합성 실패 재생은 **메모리에서만 실행되며 DB를 변경하지 않습니다.**

### 원천 샘플

`collect_local.py`가 실제 ITS 응답에서 달구벌대로 유효 링크를 추린 뒤, 순서상 균등 간격으로 최대 5건을 `traffic_source_samples`에 저장합니다. API 키는 저장하지 않습니다.

기존 DB를 사용 중이라면 업데이트된 `schema.sql`을 Neon SQL Editor에서 다시 한 번 실행해야 `failure_type` 컬럼과 `traffic_source_samples` 테이블이 추가됩니다. 기존 `traffic_daily` 데이터는 삭제되지 않습니다.
