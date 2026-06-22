@echo off
REM dataviz-prod 알람 감시 — ALARM 발생 시에만 Google Chat 알림 (평소 조용)
docker run --rm -v "C:/Users/SQI/.aws:/root/.aws:ro" -v "D:/SQ/hermes_agent:/work:ro" -e AWS_PROFILE=hermes-cw -e AWS_REGION=ap-northeast-2 -e ALERT_ONLY=1 --entrypoint python cloudwatch-mcp:0.1.4 /work/alarm_to_gchat.py
