# 배포 체크리스트

## A. 주소 만들기
- [ ] ZIP 압축 풀기
- [ ] GitHub Repository 생성
- [ ] 파일 전체 업로드
- [ ] Vercel에서 GitHub 저장소 Import
- [ ] 첫 Deploy
- [ ] `https://....vercel.app` 주소 확보

## B. ITS 인증키
- [ ] ITS 교통소통정보 API 신청
- [ ] 서비스 URL에 Vercel 주소 입력
- [ ] 활용 목적 작성
- [ ] 승인 대기

## C. Neon
- [ ] Vercel 프로젝트에 Neon 연결
- [ ] `DATABASE_URL` 존재 확인

## D. Cron
- [ ] 긴 랜덤 문자열 생성
- [ ] `CRON_SECRET` 등록

## E. ITS 승인 후
- [ ] `ITS_API_KEY` 등록
- [ ] `DATABASE_URL` 확인
- [ ] `CRON_SECRET` 확인
- [ ] Production 환경에 모두 적용
- [ ] Redeploy

## F. 연결 테스트
- [ ] `/api/health` 접속
- [ ] `ok: true`
- [ ] `itsApiKeyConfigured: true`
- [ ] `databaseConfigured: true`
- [ ] `cronSecretConfigured: true`

## G. 첫 실제 날짜
- [ ] 자동 수집 확인
- [ ] 메인 화면에 속도 표시
- [ ] Neon `traffic_daily` 1건 이상
- [ ] `collection_attempts` SUCCESS 또는 DELAYED 확인

## H. 둘째 실제 날짜
- [ ] 다음날 수집 확인
- [ ] `traffic_daily` 2건 이상
- [ ] 오늘/이전 날짜 비교 표시 확인

## I. 제출 전
- [ ] API 키가 GitHub에 없음
- [ ] DATABASE_URL이 GitHub에 없음
- [ ] CRON_SECRET이 GitHub에 없음
- [ ] 실패 시 0 km/h를 쓰지 않는 점 확인
- [ ] 마지막 정상값에 “오늘 값 아님” 안내 확인
- [ ] 서로 다른 실제 날짜 2건 캡처
