# 배포 체크리스트

### A. Neon
- [ ] Neon DB 생성/연결
- [ ] `schema.sql` 실행
- [ ] `traffic_daily` 테이블 확인
- [ ] `collection_attempts` 테이블 확인
- [ ] `collection_attempts.failure_type` 컬럼 확인
- [ ] `traffic_source_samples` 테이블 확인

### B. Vercel
- [ ] GitHub 저장소 Import
- [ ] `DATABASE_URL` 등록
- [ ] `INGEST_SECRET` 등록 (기존 `CRON_SECRET`도 호환)
- [ ] Deploy / Redeploy
- [ ] `/api/health`에서 `ok: true` 확인
- [ ] `/api/verify` 응답 확인
- [ ] `/verify` 검증 화면 확인
- [ ] `collectionMode: LOCAL_INGEST` 확인
- [ ] `expectedCollectTimeKST: 09:00` 확인

### C. 로컬 수집기
- [ ] 국내 회선 PC/NAS/서버 준비
- [ ] Python 3.12 이상 확인
- [ ] `ITS_API_KEY` 설정
- [ ] `INGEST_URL=https://.../api/ingest` 설정
- [ ] `INGEST_SECRET`을 Vercel과 동일하게 설정
- [ ] `python collect_local.py` 수동 실행 성공
- [ ] Neon에 당일 `traffic_daily` 1건 확인
- [ ] `collection_attempts`에 성공 이력 확인
- [ ] `traffic_source_samples`에 실제 원천 샘플 최대 5건 확인

### D. 09:00 스케줄
- [ ] 운영체제 스케줄러에 매일 09:00 KST 등록
- [ ] Vercel Cron이 없는지 확인
- [ ] 다음날 09:00 자동 실행 확인

### E. 과제 조건
- [ ] 날짜별 `traffic_daily` 최대 1건
- [ ] 오늘과 달력상 전날 데이터 비교
- [ ] 전날 데이터가 없으면 다른 과거 날짜와 비교하지 않음
- [ ] 실패 시 0 km/h를 저장하지 않음
- [ ] 오늘 데이터가 없을 때 마지막 정상값은 “오늘 값 아님”으로 안내

### F. 안정성/보안
- [ ] 기존 NORMAL을 DELAYED 재수집이 덮어쓰지 않는지 확인
- [ ] `/api/ingest` 인증 없이 401 확인
- [ ] `/api/attempt` 인증 없이 401 확인
- [ ] `/api/collect`가 더 이상 존재하지 않음
- [ ] `/api/test-tcp`가 더 이상 존재하지 않음
- [ ] API 키/DATABASE_URL/INGEST_SECRET이 GitHub에 없음
- [ ] `.env`가 Git에 포함되지 않음

### G. 제출 전
- [ ] 서로 다른 실제 날짜 2건 이상 확보
- [ ] 메인 화면의 전날 대비 표시 캡처
- [ ] Neon 날짜별 기록 캡처
- [ ] `/verify`에서 5개 검증 조건 캡처
- [ ] 실패 합성 재생 5종이 모두 PASS인지 확인
- [ ] 오늘/전날 실제 KST 기록이 모두 있어 전날 비교가 PASS인지 확인

## 17. 파일별 역할

```text
aleph-traffic/
├── api/
│   └── index.py                 # Vercel FastAPI: 조회/ingest/실패이력
├── tests/
│   └── test_traffic_core.py     # 로컬 교통 요약 로직 테스트
├── collect_local.py             # 국내 회선에서 매일 09:00 실행
├── traffic_core.py              # ITS 파싱/필터/통계 공통 로직
├── index.html                   # 대시보드 UI
├── verify.html                  # 제출 조건 검증 UI
├── pyproject.toml               # Vercel Python 의존성
├── vercel.json                  # API rewrite만 설정 (Cron 없음)
├── schema.sql                   # Neon DB 스키마
├── .env.example                 # 환경변수 예시
├── .gitignore                   # 비밀/로컬 파일 제외
└── README.md                    # 프로젝트 설명 + 배포/운영 체크리스트
```
