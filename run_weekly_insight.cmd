@echo off
REM dataviz-prod 주간 인사이트 리포트 — 매주 월요일 Google Chat 스페이스로 전송
docker run --rm -v "C:/Users/SQI/.aws:/root/.aws:ro" -v "D:/SQ/hermes_agent:/work:ro" -e AWS_PROFILE=hermes-cw -e AWS_REGION=ap-northeast-2 -e DAYS=7 -e POST=1 --entrypoint python cloudwatch-mcp:0.1.4 /work/weekly_insight.py
