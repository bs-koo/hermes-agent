@echo off
REM dataviz-prod 운영 대시보드 — docker build + 백그라운드(-d) 실행 래퍼
REM 접속: http://localhost:8080  (8080 이 이미 사용 중이면 아래 -p 좌측 값을 8090 등으로 변경)
REM
REM --workers 1 유지(이미지 ENTRYPOINT 고정) — 멀티워커 시 스케줄러가 워커마다 떠서 수집 중복.
REM 컨테이너는 비root(USER app)로 동작 → AWS 자격증명은 /root/.aws 가 아니라
REM   AWS_CONFIG_FILE/AWS_SHARED_CREDENTIALS_FILE env 로 경로를 명시한다(/aws 마운트).
REM
REM 서버(EC2) 이관 시:
REM   - /aws 마운트·AWS_CONFIG_FILE·AWS_SHARED_CREDENTIALS_FILE·AWS_PROFILE 제거 → 인스턴스 IAM 역할 자동(boto3 자격증명 체인)
REM   - CSV/DB 경로만 교체(UPTIME_CSV / DASH_DB env 로 흡수, 컨테이너 내부 경로는 그대로)
REM   - named volume(dataviz_dash_db)은 최초 생성 시 비어 있어야 비root 쓰기 권한이 상속된다.

REM --- 빌드 (이미지 없으면 빌드, 매번 실행해도 레이어 캐시로 빠름) ---
docker build -f Dockerfile.dashboard -t dataviz-dashboard:latest .

REM --- 기존 컨테이너 있으면 제거 후 재기동 ---
docker rm -f dataviz-dashboard >nul 2>&1

REM --- .env 가 있으면 주입(GCHAT_WEBHOOK/GEMINI_API_KEY/GOOGLE_API_KEY 등) ---
REM    docker 는 없는 --env-file 을 지정하면 에러 → 존재할 때만 옵션 추가.
set ENVFILE_OPT=
if exist ".env" set ENVFILE_OPT=--env-file .env

REM --- 실행 (장기 실행 → -d 백그라운드, 재부팅/크래시 시 자동 재시작) ---
docker run -d --name dataviz-dashboard --restart unless-stopped ^
  %ENVFILE_OPT% ^
  -v "C:/Users/SQI/.aws:/aws:ro" -e AWS_CONFIG_FILE=/aws/config -e AWS_SHARED_CREDENTIALS_FILE=/aws/credentials -e AWS_PROFILE=hermes-cw -e AWS_REGION=ap-northeast-2 ^
  -v "D:/SQ/hermes_agent/uptime_log.csv:/data/uptime_log.csv:ro" -e UPTIME_CSV=/data/uptime_log.csv ^
  -v dataviz_dash_db:/db -e DASH_DB=/db/dashboard.db ^
  -p 8080:8080 ^
  dataviz-dashboard:latest

echo.
echo 대시보드 기동됨 - 접속: http://localhost:8080
echo 로그 확인: docker logs -f dataviz-dashboard
