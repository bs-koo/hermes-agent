@echo off
REM dataviz-prod 일일 점검 digest — 매일 10:00 Google Chat 전송 (주간과 동일 포맷)
docker run --rm -v "C:/Users/SQI/.aws:/root/.aws:ro" -v "D:/SQ/hermes_agent:/work:ro" -e AWS_PROFILE=hermes-cw -e AWS_REGION=ap-northeast-2 -e POST=1 --entrypoint python cloudwatch-mcp:0.1.4 /work/daily_digest.py
