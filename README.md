# 달구벌 NOW

대구광역시 **달구벌대로**의 교통 흐름을 매일 같은 시각에 기록하고,  
**오늘과 전날의 평균 통행속도를 비교**하는 일별 교통 모니터링 프로젝트입니다.

실시간 ITS 원천 데이터는 국내 로컬 환경에서 수집하고, 가공된 결과만 Vercel API를 통해 Neon Postgres에 저장합니다.

> **운영 기준**
>
> - 공식 수집 시각: **매일 09:00 KST**
> - 공식 수집 허용 구간: **09:00 ~ 09:09 KST**
> - 비교 기준: **KST 달력상 정확한 전날**
> - 10:00까지 공식 수집 결과와 실패 보고가 모두 없으면: **MISSED**
> - 늦게 실행한 현재 데이터를 09:00 데이터로 소급 저장하지 않음

---

## 서비스

- 메인 대시보드: `https://aleph-traffic.vercel.app`
- API 상태 확인: `https://aleph-traffic.vercel.app/api/health`

---

## 프로젝트 목적

이 프로젝트는 단순히 현재 교통속도를 보여주는 것이 아니라,  
**동일한 기준 시각의 실제 교통 데이터를 하루 단위로 누적**하는 것을 목표로 합니다.

메인 대시보드에서 다음을 확인할 수 있습니다.

- 오늘 달구벌대로의 평균 통행속도
- 전날 대비 속도 변화
- 최소 / 최대 속도
- 분석에 사용된 링크 수
- ITS 원천 데이터의 최신 시각
- 최근 7일 / 최근 30일 일별 속도 추이
- 일별 NORMAL / DELAYED 상태
- 마지막 NORMAL 일별 기록
- 전체 일별 기록 수
- 오늘과 정확한 KST 전날 기록
- 전날 대비 변화량 / 변화율
- 수집 성공 / 실패 / 누락 상태

---

## 전체 구조

```text
                  매일 09:00 KST
                        │
                        ▼
              Windows 작업 스케줄러
                        │
                        ▼
                 collect_local.py
                        │
                        ▼
            국가교통정보센터 ITS API
                        │
                        ▼
                  traffic_core.py
            ┌───────────┴───────────┐
            │                       │
       링크 필터링              데이터 검증
       속도 집계                지연 여부 판정
       원천 샘플                실패 유형 분류
            │                       │
            └───────────┬───────────┘
                        ▼
                POST /api/ingest
                        │
                        ▼
                Vercel FastAPI
                        │
                        ▼
                  Neon Postgres
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
   traffic_daily   collection_   traffic_source_
                    attempts       samples
          │
          ▼
     /api/dashboard
          │
          ▼
      index.html
```

Vercel은 ITS 원천 데이터를 직접 수집하지 않습니다.

교통정보 수집은 **국내 로컬 환경**에서만 수행하며,  
Vercel은 데이터 저장·조회와 누락 여부 확인을 담당합니다.

---

## 일별 수집 방식

### 1. 09:00 로컬 수집

매일 09:00 KST에 Windows 작업 스케줄러가 `collect_local.py`를 실행합니다.

수집기는 다음 순서로 동작합니다.

```text
ITS API 호출
    ↓
달구벌대로 링크 추출
    ↓
유효 속도값 검증
    ↓
평균 / 최소 / 최대 속도 계산
    ↓
원천 데이터 지연 여부 판정
    ↓
검증용 링크 샘플 추출
    ↓
Vercel /api/ingest 전송
    ↓
Neon DB 저장
```

공식 일별 데이터는 **09:00~09:09 KST에 시작된 수집만 저장**됩니다.

예를 들어 12:00에 수동 실행하더라도 그 값을 09:00 데이터처럼 저장하지 않습니다.

---

## PC가 꺼져 있으면?

09:00에 로컬 PC가 꺼져 있으면 수집기 자체가 실행되지 않습니다.

이 경우 Vercel이 매일 **10:00 KST**에 DB만 확인합니다.

```text
09:00
PC 꺼짐
    ↓
로컬 수집 실행 안 됨
    ↓
서버에 데이터 / 실패 보고 없음

10:00
Vercel /api/check-missed 실행
    ↓
오늘 공식 기록 없음
+ FAILED 기록 없음
    ↓
MISSED 기록
```

Vercel의 10:00 작업은 **ITS API를 호출하지 않습니다.**

---

## 수집 상태

| 상태 | 의미 |
|---|---|
| `NORMAL` | 정상 수집 및 정상 원천 데이터 |
| `DELAYED` | 수집은 성공했지만 원천 데이터 지연 비율이 기준 이상 |
| `FAILED` | 수집기가 실행됐지만 ITS/API/데이터 처리 과정에서 실패 |
| `MISSED` | 10:00까지 공식 데이터와 실패 보고가 모두 도착하지 않음 |
| `MISSING` | 09:00 이후, 10:00 누락 점검 전까지 아직 결과가 없는 상태 |

실패가 발생해도 교통속도를 `0 km/h`로 저장하지 않습니다.

`0 km/h`는 실제 교통상황과 장애를 구분할 수 없기 때문입니다.

---

## 전날 대비

비교 대상은 **가장 최근의 과거 데이터가 아니라 정확히 전날 데이터**입니다.

예:

```text
2026-09-08  28.3 km/h
2026-09-09  26.6 km/h
```

결과:

```text
전날 대비 -1.7 km/h
```

반대로:

```text
09/08 데이터 있음
09/07 데이터 없음
09/06 데이터 있음
```

이라면 `09/06`을 대신 사용하지 않습니다.

이 경우 전날 비교는 **사용 불가**로 표시합니다.

---

## 교통속도 계산

ITS 응답 중 다음 조건을 만족하는 링크만 사용합니다.

- `roadName`에 `달구벌대로` 포함
- 속도 `0 < speed <= 200 km/h`

대표 통행속도는 유효 링크들의 **산술평균**입니다.

```text
평균 통행속도
= 유효 링크 speed 합계 / 유효 링크 수
```

함께 기록하는 값:

- 평균속도
- 최소속도
- 최대속도
- 유효 링크 수
- 원천 최신 시각
- 지연 링크 수
- 원천 데이터 지연 비율
- 최대 5개의 실제 원천 링크 샘플

---

## 원천 데이터 지연 판정

생성시각이 존재하는 전체 유효 링크를 검사합니다.

기준:

```text
지연 기준       : 30분
DELAYED 판정    : timestamp가 있는 링크 중
                  30% 이상이 30분 이상 오래된 경우
```

따라서 최신 링크 하나만 정상이고 나머지 데이터가 오래된 상황을 정상으로 오판하는 문제를 줄였습니다.

---

## 데이터 보존 정책

### 공식 일별 기록

`traffic_daily`는 날짜별 공식 교통 기록을 저장합니다.

한 날짜에 하나의 공식 기록만 존재합니다.

기존 NORMAL 데이터가 있는 상태에서 DELAYED 데이터를 다시 수집하더라도  
**기존 NORMAL 데이터를 DELAYED 데이터가 덮어쓰지 않습니다.**

### 수집 이력

`collection_attempts`에는 수집 시도를 별도로 기록합니다.

따라서:

```text
공식 일별 데이터
≠
수집 시도 이력
```

으로 분리됩니다.

외부 장애가 발생해도 마지막 정상값과 기존 일별 기록은 그대로 유지됩니다.

메인 대시보드에서도 다음 보존 상태를 확인할 수 있습니다.

- 마지막 NORMAL 기록의 날짜와 평균속도
- 전체 일별 공식 기록 수
- 오늘 공식 기록
- 정확한 KST 전날 공식 기록
- 전날 대비 변화량과 변화율

---

## 데이터베이스

Neon Postgres에서 다음 3개 테이블을 사용합니다.

### `traffic_daily`

날짜별 공식 교통 기록입니다.

주요 데이터:

```text
local_date
captured_at
road_name
average_speed
min_speed
max_speed
link_count
source_updated_at
source_status
```

### `collection_attempts`

실제 수집 시도 및 누락 상태를 기록합니다.

주요 데이터:

```text
attempted_at
local_date
status
failure_type
message
average_speed
link_count
source_updated_at
```

### `traffic_source_samples`

실제 원천 데이터의 일부를 증거로 남기기 위해  
매일 최대 5개의 ITS 링크 샘플을 보관합니다.

주요 데이터:

```text
local_date
captured_at
road_name
link_id
speed
source_updated_at
```

API Key, DB URL, 인증 Secret 등의 비밀정보는 저장하지 않습니다.

---

## 외부 실패 처리

수집 과정에서 다음 유형을 구분합니다.

```text
TIMEOUT
HTTP_ERROR
INVALID_JSON
EMPTY_DATA
STALE_DATA
```

실패는 정상 교통값으로 저장하지 않습니다.

수집기가 실행됐지만 실패한 경우 `collection_attempts`에 `FAILED` 이력으로 기록하고,  
기존 `traffic_daily`의 정상 일별 기록은 유지합니다.

---

## 메인 대시보드

메인 화면은 `/api/dashboard`의 데이터를 사용합니다.

### 오늘 교통상황

- 평균 통행속도
- 가장 느린 구간
- 가장 빠른 구간
- 측정 링크 수
- 혼잡 판정
- ITS 원천 최신 시각

### 일별 속도 추이

기본 화면은 **최근 7일**을 표시합니다.

사용자는 **최근 7일 / 최근 30일**을 전환해서 볼 수 있습니다.

차트 표현:

- 초록색 세로 막대: 날짜별 평균속도
- 얇은 선과 양 끝 점: 날짜별 최저~최고 속도
- hover 상세정보: 평균 / 최저 / 최고 / 구간 편차 / 전날 대비 / 링크 수 / 수집 시각 / ITS 최신 시각 / 원천 상태

최근 30일 보기에서는 날짜 라벨이 지나치게 겹치지 않도록 일부 날짜 라벨만 표시하며,  
해당 기간의 데이터 자체는 유지합니다.

### 기록 무결성

메인 화면에서 다음 두 항목도 함께 확인합니다.

#### 마지막 정상값 일별 기록 보존

- 마지막 `NORMAL` 기록
- 전체 일별 기록 수
- 실패값을 `traffic_daily`에 `0 km/h`로 저장하지 않는 정책
- 기존 `NORMAL`을 `DELAYED` 재수집으로 덮어쓰지 않는 정책

#### KST 전날 대비 실제 기록

- 오늘 공식 기록
- 정확한 전날 공식 기록
- 변화량
- 변화율

전날 공식 기록이 없으면 다른 과거 날짜를 대신 사용하지 않습니다.

---

## API

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/api` | API 정보 |
| `GET` | `/api/health` | 환경 및 서비스 상태 확인 |
| `GET` | `/api/dashboard` | 메인 대시보드 데이터 |
| `GET` | `/api/history` | 최근 일별 기록 |
| `GET` | `/api/check-missed` | 10:00 누락 검사, Vercel Cron 전용 |
| `POST` | `/api/ingest` | 로컬 정상 수집 데이터 저장 |
| `POST` | `/api/attempt` | 로컬 실패 이력 저장 |

대시보드의 최근 30일 표시를 위해 `/api/dashboard`의 일별 기록 조회 한도는 **30건**으로 사용합니다.

---

## 환경변수

### Vercel Production

```text
DATABASE_URL
INGEST_SECRET
CRON_SECRET
```

역할:

```text
DATABASE_URL
→ Neon Postgres 연결

INGEST_SECRET
→ 로컬 수집기 → Vercel 데이터 전송 인증

CRON_SECRET
→ Vercel 10:00 MISSED 점검 인증
```

`INGEST_SECRET`과 `CRON_SECRET`은 서로 다른 역할입니다.

### 로컬 수집 PC

```text
ITS_API_KEY
INGEST_URL=https://aleph-traffic.vercel.app/api/ingest
INGEST_SECRET=Vercel의 INGEST_SECRET과 동일한 값
```

로컬 PC에는 `CRON_SECRET`이 필요하지 않습니다.

실제 비밀값이 들어 있는 `.cmd`, `.bat`, 환경변수 파일은 GitHub에 업로드하지 않습니다.

---

## Windows 자동 실행

현재 운영 환경에서는 Windows 작업 스케줄러를 사용합니다.

```text
작업 이름 : ALEPH Traffic 0900 Collector
실행 주기 : 매일
실행 시각 : 09:00 KST
```

작업 스케줄러가 로컬 수집 스크립트를 실행하고, 수집 결과를 Vercel로 전송합니다.

---

## 저장소 구조

GitHub에는 서비스 동작과 이해에 필요한 파일만 유지합니다.

```text
aleph-traffic/
├── api/
│   └── index.py          # FastAPI 백엔드
│
├── collect_local.py      # 국내 로컬 ITS 수집기
├── traffic_core.py       # 파싱 / 집계 / 지연 판정 공통 로직
│
├── index.html            # 메인 대시보드
│
├── pyproject.toml        # Python 의존성
├── vercel.json           # Vercel 라우팅 / Cron 설정
└── README.md
```

`verify.html`은 사용하지 않습니다.

로컬 자동 실행용 파일은 비밀값 노출 방지를 위해 저장소에 포함하지 않습니다.

---

## 기술 스택

### Frontend

- HTML
- CSS
- Vanilla JavaScript

### Backend

- Python 3.12+
- FastAPI
- Pydantic

### Database

- Neon Serverless Postgres
- psycopg 3

### Deployment

- Vercel

### Data Source

- 국가교통정보센터 ITS 교통소통정보 API

---

## 보안 원칙

다음 값은 웹 화면과 공개 API 응답에 노출하지 않습니다.

```text
ITS_API_KEY
INGEST_SECRET
CRON_SECRET
DATABASE_URL
Authorization Header
```

`/api/ingest`와 `/api/attempt`는 Bearer Secret 인증을 사용합니다.

상세 서버 예외도 그대로 사용자에게 반환하지 않고 서버 로그와 공개 오류 메시지를 분리합니다.

---

## 핵심 설계 원칙

```text
실패값을 정상 교통값으로 위장하지 않는다.
늦게 수집한 데이터를 09:00 데이터로 소급하지 않는다.
전날 데이터가 없으면 다른 날짜로 대체하지 않는다.
기존 정상 기록을 불완전한 데이터로 덮어쓰지 않는다.
실제 원천값의 일부를 비밀정보 없이 보존한다.
```

이 프로젝트는 **매일 동일한 시점의 실제 교통 데이터를 신뢰성 있게 축적하고,  
수집 실패·누락까지 구분하여 기록하는 것**을 핵심 목표로 합니다.
