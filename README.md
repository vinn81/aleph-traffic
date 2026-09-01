# 달구벌 NOW — 최종안

대구광역시 **달구벌대로**의 실제 ITS 교통 데이터를
**하루 한 번 자동 수집**하고, Neon Postgres에 날짜별로 저장한 뒤
직전 실제 날짜와 비교하는 정보판입니다.

사이트 방문자가 ITS API를 직접 호출하지 않습니다.

```text
매일 09:30 KST
      ↓
Vercel Cron
      ↓
/api/collect
      ↓
ITS 교통소통정보 API
      ↓
달구벌대로 평균속도 계산
      ↓
Neon Postgres 저장

사용자가 사이트 접속
      ↓
/api/dashboard
      ↓
Neon DB 데이터만 조회
      ↓
오늘 / 이전 날짜 비교
```

## 파일 구조

```text
dalgubeol-now-final/
├── api/
│   └── index.py
├── index.html
├── pyproject.toml
├── vercel.json
├── schema.sql
├── .env.example
├── .gitignore
├── DEPLOY_CHECKLIST.md
├── FILE_STRUCTURE.txt
└── README.md
```

# 1. GitHub 저장소 만들기

GitHub에서 새 Repository를 만듭니다.

예:

```text
dalgubeol-now
```

이 폴더 안의 파일을 **전부 저장소 루트에** 올립니다.

중요:
- `api/index.py` 위치를 바꾸지 마세요.
- 실제 API 키나 DB 연결 문자열을 GitHub 코드에 직접 쓰지 마세요.
- `.env` 계열은 `.gitignore`로 제외되어 있습니다.

# 2. Vercel에 첫 배포해서 주소부터 확보

ITS 인증키 신청에 서비스 주소가 필요하다면
**API 키 없이 먼저 배포**해서 URL부터 만듭니다.

1. Vercel 접속
2. Add New → Project
3. GitHub의 `dalgubeol-now` 저장소 Import
4. Deploy

배포 후 예:

```text
https://dalgubeol-now.vercel.app
```

이 시점에는 API 키와 DB가 없으므로
화면에 “설정 필요”가 나와도 정상입니다.

# 3. Vercel 주소로 ITS 인증키 신청

ITS 국가교통정보센터에서
**교통소통정보 Open API 인증키**를 신청합니다.

공식 페이지:

```text
https://www.its.go.kr/opendata/opendataList?service=traffic
```

서비스 주소가 필요하면 방금 받은 Vercel URL을 입력합니다.

활용 목적 예시:

```text
대구광역시 달구벌대로의 일별 교통 통행속도를 수집하여
전날과 비교하고 데이터 수집 실패 시 마지막 정상값을 안내하는
교통 정보판 개발
```

# 4. Neon Postgres 연결

ITS 키를 기다리는 동안 DB를 준비할 수 있습니다.

Vercel 프로젝트에서:

```text
Storage / Marketplace
→ Neon
→ Connect / Create
→ 현재 프로젝트 연결
```

연결 후:

```text
Project
→ Settings
→ Environment Variables
```

에서 `DATABASE_URL`이 있는지 확인합니다.

없다면 Neon Console의 Connection Details에서 연결 문자열을 복사해
`DATABASE_URL` 이름으로 직접 등록합니다.

# 5. CRON_SECRET 만들기

`/api/collect`를 외부 사용자가 마음대로 호출하지 못하도록
비밀 문자열을 추가합니다.

생성 예:

```bash
openssl rand -hex 32
```

Vercel 환경변수에:

```text
CRON_SECRET
```

이름으로 등록합니다.

실제 값은 GitHub에 넣지 마세요.

# 6. ITS 키 승인 후 환경변수 등록

최종적으로 Vercel의 Production 환경에 아래 3개가 필요합니다.

```text
ITS_API_KEY
DATABASE_URL
CRON_SECRET
```

선택 환경변수:

```text
ITS_API_URL
```

기본 ITS 주소는 코드에 이미 들어 있으므로 보통 추가할 필요가 없습니다.

# 7. 환경변수 추가 후 Redeploy

Vercel:

```text
Deployments
→ 최근 Deployment
→ Redeploy
```

또는 GitHub에 새 Commit을 Push합니다.

# 8. 설정 상태 확인

배포 주소 뒤에:

```text
/api/health
```

를 붙입니다.

정상 예시:

```json
{
  "ok": true,
  "itsApiKeyConfigured": true,
  "databaseConfigured": true,
  "cronSecretConfigured": true,
  "expectedCollectTimeKST": "09:30",
  "runtime": "Vercel Python / FastAPI"
}
```

# 9. 하루 1회 자동 수집

`vercel.json`에 이미 다음 Cron이 포함되어 있습니다.

```json
{
  "crons": [
    {
      "path": "/api/collect",
      "schedule": "30 0 * * *"
    }
  ]
}
```

프로젝트에서는 이를 한국시간 오전 9:30 수집용으로 사용합니다.

하루 한 번 `/api/collect`가 실행되고,
ITS에서 달구벌대로 속도를 받아 Neon DB에 저장합니다.

# 10. 성공 데이터 저장

성공하거나 지연된 실제 값은:

```text
traffic_daily
```

테이블에 **날짜별 1건**으로 저장됩니다.

예:

```text
2026-09-01 | 32.4 km/h
2026-09-02 | 28.7 km/h
```

같은 날짜에 다시 정상 수집되면 그 날짜 행을 최신값으로 갱신합니다.

# 11. 실패 이력도 저장

모든 수집 시도는:

```text
collection_attempts
```

테이블에 남습니다.

예:

```text
2026-09-01 09:30 | SUCCESS
2026-09-02 09:30 | FAILED | ITS API timeout
```

그래서 데이터가 안 왔을 때도 단순히 “없음”이 아니라
“오늘 수집을 시도했지만 실패했다”라고 설명할 수 있습니다.

# 12. 오늘과 전날 비교

오늘 정상 데이터가 존재할 때만 비교합니다.

예:

```text
9/1 : 32.4 km/h
9/2 : 28.7 km/h
```

화면:

```text
▼ 3.7 km/h
이전 실제 날짜보다 느립니다
```

오늘 수집이 실패했다면
어제 값을 오늘 값으로 가장하지 않습니다.

```text
오늘 비교 불가
마지막 정상값: 9/1 32.4 km/h
※ 현재 표시값은 오늘 값이 아님
```

# 13. 지연 데이터 처리

ITS API 호출은 성공했지만
원천 데이터의 생성시각이 수집시각보다 30분 이상 오래되면:

```text
DELAYED
```

로 저장합니다.

즉 “값은 받았지만 충분히 최신인지 의심되는 상태”를 구분합니다.

# 14. DB 테이블 생성

코드가 첫 DB 접근 때 필요한 테이블을 자동 생성합니다.

따라서 보통 `schema.sql`을 직접 실행할 필요는 없습니다.

원하면 Neon SQL Editor에서 `schema.sql` 전체를 직접 실행해도 됩니다.

# 15. Neon에서 기록 확인

성공한 일별 데이터:

```sql
SELECT *
FROM traffic_daily
ORDER BY local_date DESC;
```

수집 성공/실패 이력:

```sql
SELECT *
FROM collection_attempts
ORDER BY attempted_at DESC;
```

서로 다른 실제 날짜 개수:

```sql
SELECT COUNT(*)
FROM traffic_daily;
```

`traffic_daily.local_date`가 PRIMARY KEY이므로
행 수가 성공적으로 저장된 날짜 수입니다.

# 16. 메인 화면 상태

## 정상

```text
오늘 데이터 정상
오늘 평균속도 28.7 km/h
전날 대비 ▼ 3.7 km/h
```

## 오늘 수집 전

```text
오늘 수집 전
마지막 정상값을 참고용으로 표시
```

## 수집 예정 시간이 지났는데 Cron 기록 없음

```text
오늘 수집 기록 없음
Cron 실행 상태 확인 필요
```

## ITS API 수집 실패

```text
오늘 수집 실패
마지막 정상값 32.4 km/h
※ 현재 값은 오늘 값이 아님
```

# 17. 수동 수집 테스트

`/api/collect`는 CRON_SECRET으로 보호되어 있습니다.

브라우저에서 그냥 열었을 때 401이 나오는 것은 정상입니다.

macOS/Linux:

```bash
curl -H "Authorization: Bearer YOUR_CRON_SECRET" \
  https://YOUR_PROJECT.vercel.app/api/collect
```

Windows PowerShell:

```powershell
Invoke-RestMethod `
  -Uri "https://YOUR_PROJECT.vercel.app/api/collect" `
  -Headers @{ Authorization = "Bearer YOUR_CRON_SECRET" }
```

# 18. 과제용 실제 날짜 2건 확보

첫째 날:
- 자동 수집 후 메인 화면 확인
- `traffic_daily`에 1건 확인

둘째 날:
- 다음날 자동 수집 확인
- `traffic_daily`에 2건 확인
- 메인 화면의 오늘/이전 날짜 비교 확인

# 핵심 환경변수

| 이름 | 필수 | GitHub 공개 금지 |
|---|---|---|
| `ITS_API_KEY` | O | O |
| `DATABASE_URL` | O | O |
| `CRON_SECRET` | O | O |
| `ITS_API_URL` | 선택 | 보통 불필요 |

# API 주소

설정 확인:

```text
/api/health
```

화면 데이터:

```text
/api/dashboard
```

저장 이력:

```text
/api/history
```

자동 수집:

```text
/api/collect
```

# 데이터 계산 방식

ITS 교통소통정보 API에서 대구 영역을 조회한 뒤
`roadName`에 `달구벌대로`가 포함된 실제 링크만 추출합니다.

그 링크들의 유효 `speed` 평균을
그날의 **달구벌대로 평균 통행속도**로 기록합니다.

인증키 승인 후 실제 ITS 응답에서
도로명이 다르게 표시되는 경우에는
`api/index.py`의:

```python
ROAD_KEYWORD = "달구벌대로"
```

부분을 수정하면 됩니다.
