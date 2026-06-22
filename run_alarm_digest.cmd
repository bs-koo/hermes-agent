@echo off
REM dataviz-prod 일일 다이제스트 — 항상 전송 (정상 여부 확인)
docker run --rm -v "C:/Users/SQI/.aws:/root/.aws:ro" -v "D:/SQ/hermes_agent:/work:ro" -e AWS_PROFILE=hermes-cw -e AWS_REGION=ap-northeast-2 --entrypoint python cloudwatch-mcp:0.1.4 /work/alarm_to_gchat.py
