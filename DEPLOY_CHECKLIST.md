# 배포 체크리스트

## A. Neon
- [ ] Neon DB 생성/연결
- [ ] `schema.sql` 실행
- [ ] `traffic_daily` 테이블 확인
- [ ] `collection_attempts` 테이블 확인

## B. Vercel
- [ ] GitHub 저장소 Import
- [ ] `DATABASE_URL` 등록
- [ ] `INGEST_SECRET` 등록 (기존 `CRON_SECRET`도 호환)
- [ ] Deploy / Redeploy
- [ ] `/api/health`에서 `ok: true` 확인
- [ ] `collectionMode: LOCAL_INGEST` 확인
- [ ] `expectedCollectTimeKST: 09:00` 확인

## C. 로컬 수집기
- [ ] 국내 회선 PC/NAS/서버 준비
- [ ] Python 3.12 이상 확인
- [ ] `ITS_API_KEY` 설정
- [ ] `INGEST_URL=https://.../api/ingest` 설정
- [ ] `INGEST_SECRET`을 Vercel과 동일하게 설정
- [ ] `python collect_local.py` 수동 실행 성공
- [ ] Neon에 당일 `traffic_daily` 1건 확인
- [ ] `collection_attempts`에 성공 이력 확인

## D. 09:00 스케줄
- [ ] 운영체제 스케줄러에 매일 09:00 KST 등록
- [ ] Vercel Cron이 없는지 확인
- [ ] 다음날 09:00 자동 실행 확인

## E. 과제 조건
- [ ] 날짜별 `traffic_daily` 최대 1건
- [ ] 오늘과 달력상 전날 데이터 비교
- [ ] 전날 데이터가 없으면 다른 과거 날짜와 비교하지 않음
- [ ] 실패 시 0 km/h를 저장하지 않음
- [ ] 오늘 데이터가 없을 때 마지막 정상값은 “오늘 값 아님”으로 안내

## F. 안정성/보안
- [ ] 기존 NORMAL을 DELAYED 재수집이 덮어쓰지 않는지 확인
- [ ] `/api/ingest` 인증 없이 401 확인
- [ ] `/api/attempt` 인증 없이 401 확인
- [ ] `/api/collect`가 더 이상 존재하지 않음
- [ ] `/api/test-tcp`가 더 이상 존재하지 않음
- [ ] API 키/DATABASE_URL/INGEST_SECRET이 GitHub에 없음
- [ ] `.env`가 Git에 포함되지 않음

## G. 제출 전
- [ ] 서로 다른 실제 날짜 2건 이상 확보
- [ ] 메인 화면의 전날 대비 표시 캡처
- [ ] Neon 날짜별 기록 캡처
